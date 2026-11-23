# Contract: Secret Detector Process

A long-lived child process of the proxy that wraps the gitleaks Go library (research.md R7). Source:
`sidecar/gitleaks/` (Go module, gitleaks pinned in `go.mod`). Rules: `src/claude_pii_proxy/detection/gitleaks.toml`,
which extends gitleaks' default rules.

## Lifecycle

- Started by the proxy at startup: `gitleaks-sidecar <rules file>`, with an empty environment and `/` as working
  directory, so no `GITLEAKS_*` variable, `.gitleaks.toml` or `.gitleaksignore` can change the rules.
- First stdout line: `{"ready": true, "gitleaks": "<module version>", "rules": <n>}`. The proxy refuses to start
  if the version differs from the pinned one or the line doesn't arrive within 5 s.
- `IgnoreGitleaksAllow` is on, so a `gitleaks:allow` comment in the user's data never hides a secret.
  `MaxDecodeDepth` is 5, as in the gitleaks CLI.
- Exits 0 when stdin closes. A non-zero exit, a malformed response, or no response within 10 s fails the
  request in flight with `503` (http-interface.md). The next request starts a fresh process.

## Protocol

One JSON object per line, UTF-8. One request in flight at a time; the proxy serialises calls.

Request: `{"id": <int>, "text": "<cache-miss leaves joined by separator lines>"}`

Response:
`{"id": <same>, "findings": [{"rule": "<RuleID>", "secret": "<text>", "start_line": <n>, "end_line": <n>, "tags": [...]}]}`

## Finding → span

- `start_line` and `end_line` are 0-based (gitleaks' own numbering, observed when building the detector).
- The proxy locates `secret` in the text between `start_line` and `end_line`; each occurrence becomes a
  `SECRET` span. Columns aren't used: they were off by one in S2.
- A finding tagged `decoded:*` has a `secret` that doesn't occur in the text: it was found inside base64, hex or
  percent-encoded data. The encoded segment must be replaced instead. If that segment can't be located, the
  request fails closed (FR-007). In S2 the encoded form was also caught by `generic-api-key`; this rule covers
  the case where it isn't.
