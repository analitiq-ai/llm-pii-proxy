# CLAUDE.md

Local proxy for Claude Code that pseudonymizes PII outbound and restores it inbound. See README.md
for the design constraints. They are requirements, not suggestions.

- Python 3.13, managed with `uv`. Run everything through `uv run`.
- Layout: `src/claude_pii_proxy/`, tests in `tests/`.
- Upstream HTTP is mocked with `respx` in tests. Tests never call the real API.
- Never log request/response bodies or the PII mapping.
- Any scrubbing failure rejects the request (fail closed). Never forward unscrubbed content.
