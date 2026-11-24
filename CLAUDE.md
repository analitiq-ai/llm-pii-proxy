# CLAUDE.md

Local proxy for Claude Code that pseudonymizes PII outbound and restores it inbound. README.md
§ Features and § Limits state the design constraints. They are requirements, not suggestions.

- Python 3.13, managed with `uv`. Run everything through `uv run`.
- Layout: `src/llm_pii_proxy/`, tests in `tests/`.
- Upstream HTTP is mocked with `respx` in tests. Tests never call the real API.
- Never log request/response bodies or the PII mapping.
- Any scrubbing failure rejects the request (fail closed). Never forward unscrubbed content.
- Build the secret detector once: `scripts/build-secret-detector.sh` (tests need `.tools/gitleaks-sidecar`).
- `uv run pytest` runs the default suite. `uv run pytest -m benchmark` runs the recall (SC-003) and latency
  (SC-004) benchmarks.
- `uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`.
- `scripts/e2e/` is manual: it calls the real Anthropic API and its captures hold real
  values. Never run it from tests.
