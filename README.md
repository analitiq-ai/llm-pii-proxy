# claude-pii-proxy

A local HTTP proxy between Claude Code and the Anthropic API. It swaps PII in outbound requests for
stable placeholders and puts the original values back in responses, so the PII never leaves the machine
and Claude can still work on files that contain it.

```
Claude Code ──ANTHROPIC_BASE_URL──▶ claude-pii-proxy ──▶ api.anthropic.com
            ◀── restored values ───                  ◀── placeholders ──
```

## Design constraints

- **Reversible, not one-way.** `jane@acme.com` becomes something like `PII_EMAIL_1`. The mapping is
  stable for the whole session, so the same value always gets the same placeholder across turns.
  Otherwise prompt caching breaks and Claude loses track of which entity is which.
- **Every content-bearing field is scrubbed:** `system`, message text, `tool_result` content, and
  `tool_use` inputs replayed in history.
- **Responses are restored:** text blocks and `tool_use` inputs. Claude Code acts on those, so a write
  or edit must hit disk with the real values.
- **Streaming (SSE):** a placeholder can be split across `content_block_delta` events, and so can the
  `input_json_delta` fragments of tool inputs. Restoration has to buffer across deltas and never emit a
  partial placeholder.
- **Fail closed:** if a request cannot be scrubbed, it is rejected, never forwarded unscrubbed.
- **Mapping stays local.** It lives in memory only and is never logged.

## Setup

```bash
uv sync
# the spaCy NER model (en_core_web_lg) is a locked dependency
```

## Usage

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:<port>
claude
```

## Development

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```
