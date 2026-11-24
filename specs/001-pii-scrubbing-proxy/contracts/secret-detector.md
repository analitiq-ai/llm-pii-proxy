# Contract: Secret Detector Process

A long-lived child process of the proxy that wraps the gitleaks Go library (research.md R7). Source:
`sidecar/gitleaks/` (Go module, gitleaks pinned in `go.mod`). Rules: `src/llm_pii_proxy/detection/gitleaks.toml`,
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

Request: `{"id": <int>, "texts": ["<cache-miss leaf>", ...]}`. Each leaf is scanned on its own, so a multi-line
secret can never span two leaves and no line-to-leaf mapping is needed.

Response: `{"id": <same>, "findings": [[<finding>, ...], ...]}`, one list per request text, where a finding is
`{"rule", "secret", "start_line", "end_line", "start_column", "end_column", "tags"}`.

## Finding → span

- `start_line` and `end_line` are 0-based (gitleaks' own numbering, observed when building the detector).
  `end_line` can understate a multi-line secret (a private key at the end of a text with no final newline
  reports `end_line` 0), so the search runs to `max(end_line, start_line + newlines in secret)`.
- The proxy locates `secret` in the text between `start_line` and `end_line`; each occurrence becomes a
  `SECRET` span. Columns aren't used for this: they are 1-based on a text's first line and shifted by one on
  later lines.
- A finding tagged `decoded:*` has a `secret` that doesn't occur in the text: it was found inside base64, hex or
  percent-encoded data. Its columns point at the encoded segment (approximately, as above). The proxy widens
  that column range to the whole run of encoding characters (`A-Z a-z 0-9 + / = _ - %`) that overlaps it, and
  replaces the run. Over-scrubbing a few characters is reversible; leaving part of the encoding is not.
- A finding the proxy can't locate fails the request closed (FR-007). In S2 the encoded form was also caught by
  `generic-api-key`; the decoded rule covers the case where it isn't.
