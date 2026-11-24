# llm-pii-proxy

A local HTTP proxy between Claude Code and the Anthropic API. It swaps PII and secrets in outbound requests
for stable placeholders and puts the real values back in responses, so they never leave the machine and
Claude can still work on files that contain them.

```
Claude Code ──ANTHROPIC_BASE_URL──▶ llm-pii-proxy ──▶ api.anthropic.com
            ◀── restored values ───                  ◀── placeholders ──
```

`jane@acme.com` reaches Anthropic as `PII_EMAIL_0001`. When Claude writes `PII_EMAIL_0001` into a reply or a
file edit, the proxy turns it back into `jane@acme.com` before Claude Code sees it.

## Features

- **Local detection**, nothing sent anywhere to decide what is sensitive:
  - personal data with Presidio and spaCy NER: names, emails, phone numbers, postal addresses, payment
    cards, IBANs, national IDs, IP addresses;
  - secrets with a gitleaks sidecar: API keys, tokens, private keys, credentials in `.env` files.
- **Reversible and stable.** One value keeps one placeholder for the whole session, so Claude can tell
  entities apart across turns and Anthropic's prompt cache keeps working.
- **Everything that carries content is scrubbed:** the system prompt, messages, tool results, and tool
  inputs replayed in history.
- **Everything Claude Code acts on is restored:** text, and tool inputs such as file writes and edits, so
  files on disk get the real values.
- **Streaming-safe.** A placeholder split across stream events is buffered and never emitted half-restored.
- **Fail closed.** If a request can't be scrubbed it is rejected, never forwarded raw. Unknown API routes
  are refused for the same reason.
- **Your own lists.** Values that must always be scrubbed (your name, username, email) and words that must
  never be (`Django`, `Paris`), in a file kept outside any repo.
- **Optional confidential pass.** A model running in [Privatemode](https://www.privatemode.ai)'s confidential
  computing environment finds identifying descriptions no pattern can, such as "the only left-handed
  cardiologist in Tralee". It sees only text the local pass already scrubbed. Off by default.
- **Works with Claude Code's own features:** subscription (OAuth) and API-key logins, extended thinking
  across turns, prompt caching. Claude is told placeholders stand in for real values, so it doesn't refuse
  tasks that involve them.
- **Mapping stays local.** It lives in memory only, per session, and is never logged. Each response carries
  an `x-pii-proxy-scrubbed` header with counts per category, never values.
- **Loopback only.** The proxy refuses to listen on anything but `127.0.0.1` or `::1`.

## Install

Every step is a command; an agent can run them top to bottom. Linux or macOS, x86-64 or ARM.

1. Prerequisites: `git`, `curl`, `tar`, and [uv](https://docs.astral.sh/uv/) (`uv --version`). uv installs
   Python 3.13 itself if needed. The build step downloads a pinned, checksum-verified Go toolchain into
   `.tools/`; nothing is installed system-wide.

2. Clone, install dependencies (includes the spaCy NER model, several hundred MB) and build the secret detector:

   ```bash
   git clone https://github.com/analitiq-ai/llm-pii-proxy.git
   cd llm-pii-proxy
   uv sync
   scripts/build-secret-detector.sh
   ```

3. Optional but recommended: list your own identifiers. NER can't know that your OS username in
   `/home/<user>/…` paths is a person. Keep the file outside any repository:

   ```bash
   mkdir -p ~/.config/llm-pii-proxy
   cat > ~/.config/llm-pii-proxy/lists.toml <<'EOF'
   never_scrub = []

   [always_scrub]
   PERSON = ["<your name>", "<your OS username>"]
   EMAIL = ["<your git email>"]
   EOF
   ```

   An invalid file stops the proxy at startup; a missing file means empty lists.

4. Start the proxy from the repo root (it finds `.tools/gitleaks-sidecar` relative to it) and leave it
   running, in its own terminal or a `tmux` session:

   ```bash
   uv run python -m llm_pii_proxy      # listens on 127.0.0.1:8787
   ```

5. Check it, from another shell. This request goes through the proxy to Anthropic:

   ```bash
   curl -sI -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8787/api/hello    # expect 200
   ```

6. Point Claude Code at it. For one shell:

   ```bash
   export ANTHROPIC_BASE_URL=http://127.0.0.1:8787
   export CLAUDE_CODE_ATTRIBUTION_HEADER=0
   claude
   ```

   For every session, add the same two variables to the `env` block of `~/.claude/settings.json`:

   ```json
   {
     "env": {
       "ANTHROPIC_BASE_URL": "http://127.0.0.1:8787",
       "CLAUDE_CODE_ATTRIBUTION_HEADER": "0"
     }
   }
   ```

   Claude Code then can't reach Anthropic while the proxy is stopped. That is the point: nothing bypasses
   it. `CLAUDE_CODE_ATTRIBUTION_HEADER=0` stops Claude Code putting an attribution block first in the system
   prompt. Anthropic strips that block only when it arrives unchanged, and scrubbing could change it.

### Optional: confidential pass

Needs a Privatemode API key. Run its proxy, pinned by digest, on loopback (the key stays in that container;
llm-pii-proxy never sees it):

```bash
docker run -d -p 127.0.0.1:8080:8080 \
  ghcr.io/edgelesssys/privatemode/privatemode-proxy@sha256:cf046abd3f6cbccccc69cc742533607235b756cb497f8d23d4b02f42fc223334 \
  --api-key <key>
```

Then start llm-pii-proxy with the pass on:

```bash
PII_PROXY_CONFIDENTIAL_ENABLED=true uv run python -m llm_pii_proxy
```

If Privatemode is down or rate-limited, requests fail with `503` rather than skip the pass.

## Configuration

Environment variables, all optional. Invalid values stop the proxy at startup.

| Variable | Default | Purpose |
|---|---|---|
| `PII_PROXY_HOST` | `127.0.0.1` | Listen address; must be loopback |
| `PII_PROXY_PORT` | `8787` | Listen port |
| `PII_PROXY_UPSTREAM_URL` | `https://api.anthropic.com` | Where scrubbed requests go |
| `PII_PROXY_MIN_SCORE` | `0.4` | Presidio confidence threshold |
| `PII_PROXY_SESSION_IDLE_TTL_S` | `43200` | Idle time before a session's mapping is dropped |
| `PII_PROXY_SECRET_DETECTOR_PATH` | `.tools/gitleaks-sidecar` | Built by `scripts/build-secret-detector.sh` |
| `PII_PROXY_LISTS_PATH` | `~/.config/llm-pii-proxy/lists.toml` | Always/never-scrub lists |
| `PII_PROXY_CONFIDENTIAL_ENABLED` | `false` | Turn on the confidential pass |
| `PII_PROXY_CONFIDENTIAL_URL` | `http://127.0.0.1:8080` | Privatemode proxy |
| `PII_PROXY_CONFIDENTIAL_MODEL` | `gpt-oss-120b` | Model for the pass |
| `PII_PROXY_CONFIDENTIAL_TIMEOUT_S` | `30` | Per request |
| `PII_PROXY_CONFIDENTIAL_CHUNK_CHARS` | `6000` | Text per request; larger chunks miss more |
| `PII_PROXY_CONFIDENTIAL_CONCURRENCY` | `4` | Requests in flight |

## Limits

- **Claude sees whole placeholders, never part of a value.** Tasks that need the literal value (check this
  email's format, fix a regex for these phone numbers) can't succeed. Tasks on part of a value (group by
  email domain, take initials) are out of scope: a part a tool extracts reaches Anthropic unscrubbed.
- **Tool definitions, including MCP tool descriptions, are forwarded unscrubbed.**
- **Restarting the proxy loses the mapping.** A conversation in progress may then fail with a signature
  error from Anthropic; start a new conversation. The proxy log says so.
- **Detection is not perfect.** Local detection misses some values, and the confidential pass catches about
  three in four identifying descriptions in realistic sessions.

## Development

See [CLAUDE.md](CLAUDE.md). The design spec, validation scenarios and research notes are in
[specs/001-pii-scrubbing-proxy](specs/001-pii-scrubbing-proxy/).
