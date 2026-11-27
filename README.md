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
- **Your own lists.** An `always_scrub` list (your name, username, email) and a `never_scrub` list (`Django`,
  `Paris`), in a file you keep outside any repo.
- **Images and PDFs become text before they leave.** Base64 images and PDFs are converted to text on
  Privatemode: each PDF page is read to Markdown by an OCR model, and each image is described by a vision
  model and, if it is a document, read to Markdown as well. The text is then scrubbed like any other, and
  Anthropic gets only that text. Without conversion such a request is refused. Off by default.
- **Works with Claude Code's own features:** subscription (OAuth) and API-key logins, extended thinking
  across turns, prompt caching. Claude is told placeholders stand in for real values, so it doesn't refuse
  tasks that involve them.
- **Mapping stays local.** It lives in memory only, per session, and is never logged. Each response carries
  an `x-pii-proxy-scrubbed` header with counts per category, never values.
- **Optional payload tracing.** Writes what each Messages and token-count request sent to Anthropic and what
  came back to local JSON Lines files, one per session, to show what the proxy changed. These legs hold
  what Anthropic saw: placeholders where the proxy scrubbed, and real values in anything it does not scrub
  (see [Limits](#limits)). The client legs, what Claude Code sent and what it got back, hold real values and
  need a second opt-in; with both sides on, comparing them shows what was scrubbed and what was restored.
  Owner-only files outside any git repository; a session's file is deleted once it has gone unwritten for
  the retention period (24 hours by default). Tracing never changes or fails a request. Off by
  default.
- **Loopback only.** The proxy listens only on a loopback IP address (`127.0.0.0/8` or `::1`). Host names,
  `localhost` included, are rejected: a name can resolve to a non-loopback address.

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
   `/home/<user>/…` paths is a person. Keep the file outside any repository. This writes a template only
   if the file doesn't exist yet:

   ```bash
   mkdir -p ~/.config/llm-pii-proxy
   [ -e ~/.config/llm-pii-proxy/lists.toml ] || cat > ~/.config/llm-pii-proxy/lists.toml <<'EOF'
   never_scrub = []

   [always_scrub]
   PERSON = ["<your name>", "<your OS username>"]
   EMAIL = ["<your git email>"]
   EOF
   ```

   Replace each `<…>` with the real value; an agent asks the user for them rather than guessing. An
   invalid file stops the proxy at startup; a missing file means empty lists.

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

### Optional: image and PDF conversion

Uses the same Privatemode proxy, and `pdftoppm` (`poppler-utils` on Debian and Ubuntu, `poppler` on Homebrew)
to render PDF pages; the proxy refuses to start without it.

```bash
PII_PROXY_MEDIA_ENABLED=true uv run python -m llm_pii_proxy
```

A converted block is replaced in place by a text block that says it was converted. The text is kept in
memory (the most recent `PII_PROXY_MEDIA_CACHE_ENTRIES`), so a conversation that replays an image or PDF
gets the same text without converting it again, and keeps Anthropic's prompt cache. Images and PDFs given by URL or file id are refused, as are media types other than PNG, JPEG, GIF,
WebP and PDF. If Privatemode is down, requests fail with `503`.

| Variable | Default | Meaning |
|---|---|---|
| `PII_PROXY_MEDIA_ENABLED` | `false` | convert images and PDFs; when off, a request holding one is refused with `400` |
| `PII_PROXY_MEDIA_OCR_MODEL` | `deepseek-ocr-2` | reads PDF pages and document images to Markdown |
| `PII_PROXY_MEDIA_VISION_MODEL` | `glm-5.3-flash` | describes images and tells whether one is a document |
| `PII_PROXY_MEDIA_CONCURRENCY` | `4` | Privatemode calls in flight for conversion |
| `PII_PROXY_MEDIA_CACHE_ENTRIES` | `256` | converted images and PDFs kept in memory |

The URL and timeout are `PII_PROXY_CONFIDENTIAL_URL` and `PII_PROXY_CONFIDENTIAL_TIMEOUT_S`.

## Configuration

All settings are optional `PII_PROXY_*` environment variables; invalid values stop the proxy at startup. The
full list with defaults is in [quickstart.md](specs/001-pii-scrubbing-proxy/quickstart.md#configuration-environment).

Payload tracing:

| Variable | Default | Meaning |
|---|---|---|
| `PII_PROXY_TRACE_LEGS` | empty: tracing off | comma-separated legs to record: `client_in` (sent by Claude Code), `upstream_out` (sent to Anthropic), `upstream_in` (Anthropic's response), `client_out` (returned to Claude Code); streamed responses are assembled into one message |
| `PII_PROXY_TRACE_RAW` | `false` | must be `true` to record `client_in` or `client_out`, which hold real values; without it the proxy refuses to start |
| `PII_PROXY_TRACE_DIR` | `~/.local/state/llm-pii-proxy/traces` | must be outside any git working tree, mode 0700 and yours; created if missing |
| `PII_PROXY_TRACE_TTL_H` | `24` | hours a trace file is kept after its last write |

Each line is one leg of one request: `time`, `session_id`, `request_id` (shared by the legs of a request),
`leg`, `route`, `status`, `complete` (false for a stream that ended early or with an error), `payload`.
`status` is what the client got on the client legs, and Anthropic's on the others, or the proxy's own 502
when Anthropic was unreachable. A Messages or token-count request the proxy rejects before forwarding writes
at most `client_in`; one with no session id goes to `_no-session.jsonl`. Every startup with tracing on logs
a warning naming the legs and the directory, and saying so when real values are written to disk.

### Trace viewer

Run it with the proxy's environment and open http://127.0.0.1:8765 (`PII_PROXY_VIEWER_PORT` changes
the port):

```bash
uv run python -m llm_pii_proxy.viewer
```

It reads `PII_PROXY_TRACE_DIR`. The page lists its requests, newest first; select one to see two
side-by-side comparisons: Sent (`client_in` against `upstream_out`: the replaced values and the notice the
proxy adds to `system`) and Received (`upstream_in` against `client_out`: what was restored). A leg that
was not recorded is labelled so, an incomplete one is flagged, and lines that are not trace records are
counted per file. Reload the page to see new records. The viewer listens on 127.0.0.1 only and answers only
requests addressed to `127.0.0.1:<port>` or `localhost:<port>`, so a website open in the same browser cannot
read the traces. The page writes trace content into the page only as text.

## Limits

- **Claude sees whole placeholders, never part of a value.** Tasks that need the literal value (check this
  email's format, fix a regex for these phone numbers) can't succeed. Tasks on part of a value (group by
  email domain, take initials) are out of scope: a part a tool extracts reaches Anthropic unscrubbed.
- **Tool definitions, including MCP tool descriptions, are forwarded unscrubbed.**
- **Claude never sees an image or PDF itself**, only the text converted from it: layout, handwriting and
  fine detail in a chart can be lost.
- **Restarting the proxy loses the mapping.** A conversation in progress may then fail with a signature
  error from Anthropic; start a new conversation. The proxy log says so.
- **Detection is not perfect.** Local detection misses some values.

## Development

See [CLAUDE.md](CLAUDE.md). The design spec, validation scenarios and research notes are in
[specs/001-pii-scrubbing-proxy](specs/001-pii-scrubbing-proxy/).
