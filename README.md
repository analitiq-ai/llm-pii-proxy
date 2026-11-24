# llm-pii-proxy

A local HTTP proxy between Claude Code and the Anthropic API. It swaps PII in outbound requests for
stable placeholders and puts the original values back in responses, so the PII never leaves the machine
and Claude can still work on files that contain it.

```
Claude Code ──ANTHROPIC_BASE_URL──▶ llm-pii-proxy ──▶ api.anthropic.com
            ◀── restored values ───                  ◀── placeholders ──
```

## Design constraints

- **Reversible, not one-way.** `jane@acme.com` becomes something like `PII_EMAIL_0001`. The mapping is
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

- **Whole values only.** Claude sees a placeholder, never part of a value, so tasks such as "group
  customers by email domain" need a tool that reads the file.

## Setup

```bash
uv sync                               # the spaCy NER model (en_core_web_lg) is a locked dependency
scripts/build-secret-detector.sh      # builds .tools/gitleaks-sidecar with a pinned Go toolchain
```

Optionally, list values that must always or never be scrubbed in `~/.config/llm-pii-proxy/lists.toml`
(keep it outside any repository):

```toml
never_scrub = ["Django", "Paris"]

[always_scrub]
PERSON = ["<your name>", "<your OS username>"]
EMAIL = ["<your git email>"]
```

## Usage

```bash
uv run python -m llm_pii_proxy     # listens on 127.0.0.1:8787
```

In another shell:

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8787
export CLAUDE_CODE_ATTRIBUTION_HEADER=0
claude
```

Configuration variables and validation scenarios are in
[quickstart.md](specs/001-pii-scrubbing-proxy/quickstart.md).

## Development

See [CLAUDE.md](CLAUDE.md).
