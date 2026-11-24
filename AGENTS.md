# AGENTS.md

For coding agents (Claude Code, Codex, Cursor and others).

- **Installing llm-pii-proxy for a user:** follow [README.md § Install](README.md#install) top to bottom.
  Every step is a command. Ask the user before editing `~/.claude/settings.json`; once the proxy is the
  base URL, Claude Code can't reach Anthropic while the proxy is stopped. Never ask for, read or store a
  Privatemode API key: it belongs in the Privatemode container only.
- **Working on this repository:** the rules in [CLAUDE.md](CLAUDE.md) apply to every agent, not only
  Claude.
