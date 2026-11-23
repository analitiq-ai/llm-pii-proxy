# Quickstart & Validation: PII-Scrubbing Proxy

A run-and-validate guide. Contracts: [http-interface](contracts/http-interface.md),
[placeholder-format](contracts/placeholder-format.md), [confidential-detection](contracts/confidential-detection.md),
[secret-detector](contracts/secret-detector.md).

## Prerequisites

- `uv`, with the project synced (`uv sync`). The spaCy model is a locked dependency.
- The secret detector: `scripts/build-secret-detector.sh` (downloads pinned Go into `.tools/`, gitignored, and
  builds `.tools/gitleaks-sidecar`). The proxy refuses to start on a gitleaks version mismatch.
- Story 2 only: a Privatemode API key, and its proxy running, pinned by digest (recorded by spike S3):
  `docker run -p 127.0.0.1:8080:8080 ghcr.io/edgelesssys/privatemode/privatemode-proxy@sha256:<digest> --api-key <key>`.

## Configuration (environment)

| Variable | Default | Purpose |
|---|---|---|
| `PII_PROXY_HOST` | `127.0.0.1` | must be loopback |
| `PII_PROXY_PORT` | `8787` | |
| `PII_PROXY_UPSTREAM_URL` | `https://api.anthropic.com` | |
| `PII_PROXY_MIN_SCORE` | `0.4` | Presidio score threshold |
| `PII_PROXY_SESSION_IDLE_TTL_S` | `43200` | whole-session eviction |
| `PII_PROXY_SECRET_DETECTOR_PATH` | `.tools/gitleaks-sidecar` | |
| `PII_PROXY_LISTS_PATH` | `~/.config/claude-pii-proxy/lists.toml` | operator lists (FR-018). Absent file means empty lists; an invalid file fails startup |
| `PII_PROXY_CONFIDENTIAL_ENABLED` | `false` | Story 2 switch (FR-015) |
| `PII_PROXY_CONFIDENTIAL_URL` | `http://127.0.0.1:8080` | Privatemode proxy |
| `PII_PROXY_CONFIDENTIAL_MODEL` | `glm-flash-latest` | |
| `PII_PROXY_CONFIDENTIAL_TIMEOUT_S` | `30` | |
| `PII_PROXY_CONFIDENTIAL_CHUNK_CHARS` | `24000` | |
| `PII_PROXY_CONFIDENTIAL_CONCURRENCY` | `4` | |

Invalid values fail at startup.

Operator lists, kept outside any repository:

```toml
never_scrub = ["Django", "Paris"]

[always_scrub]
PERSON = ["<your name>", "<your OS username>"]
EMAIL = ["<your git email>"]
```

## Run

```bash
uv run python -m claude_pii_proxy
```

In another shell:

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8787
export CLAUDE_CODE_ATTRIBUTION_HEADER=0   # proxy must not alter Claude Code's first system block (research R3)
claude
```

## Validation scenarios

| # | Scenario | Steps | Expected |
|---|---|---|---|
| V1 | Outbound contains no seeded values (SC-001) | `uv run pytest tests/integration -k no_leak` | Captured upstream bodies contain none of the corpus values |
| V2 | Round trip on disk (US1 scenarios 1–3, SC-002) | In a scratch project with `fixtures/pii-project/`, ask Claude to summarise `customers.csv`, then edit an unrelated line in it and in `.env` | Summary shows real values; `git diff` shows only the requested change; the key in `.env` is intact |
| V3 | Split placeholder in a stream (US1 scenario 5) | `uv run pytest tests/unit -k holdback` | No partial or raw token is emitted for any split position |
| V4 | Fail closed (FR-007, SC-007) | `uv run pytest tests/integration -k fail_closed` | Every injected detector fault gives a 4xx/5xx with zero upstream calls |
| V5 | Thinking plus tool use across turns (research R3) | With extended thinking on, run a 5+ turn task involving file reads and edits on PII-bearing files | No `invalid signature` / `cannot be modified` errors |
| V6 | Scrub report visible (FR-017) | `curl -si` a messages request via the proxy | `x-pii-proxy-scrubbed` header present; proxy log line shows counts only |
| V7 | Confidential pass (US2) | Enable the pass; `uv run pytest tests/integration -k confidential` (mocked); then V2 with `fixtures/context-pii/` against the real Privatemode proxy | Context-dependent phrases replaced; the Privatemode request log shows only already-scrubbed text |
| V8 | Confidential pass down (FR-014) | Stop the Privatemode proxy, send a request | `503`, nothing forwarded to Anthropic |
| V9 | Detection benchmark (SC-003, SC-005) | `uv run pytest tests/benchmark` | Recall at or above the spec targets; report printed |
| V10 | Task quality (SC-008, FR-019) | Run the fixed task set in `scripts/e2e/` with and without the proxy, including value-reuse tasks | Proxy success rate ≥ 95% of direct (3 runs per task per mode, pass checks fixed in advance); no planted value in captured traffic; username absent from `system` |

## Known limits

- After a proxy restart, a conversation already in progress may get signature errors (research R3). The error
  is Anthropic's, forwarded unchanged; the proxy log names the session and says to start a new Claude Code
  conversation.
- Tasks that need the model to see a literal value (validate this email's format, fix a regex for these phone
  numbers) can't succeed. Tasks that operate on part of a value (group by email domain, take a name's
  initials) are out of scope: a part extracted by a tool reaches Anthropic unscrubbed. Tool definitions, including MCP tool descriptions, are forwarded unscrubbed.
- With Story 2 enabled, the first request of a long resumed session may hit Privatemode's 200k-prompt-token-per-minute
  limit. Claude Code retries the resulting 503.
