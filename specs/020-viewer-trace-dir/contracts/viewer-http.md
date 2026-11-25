# Contract: viewer HTTP interface

Served by `python -m llm_pii_proxy.viewer` on `http://<PII_PROXY_HOST>:<PII_PROXY_VIEWER_PORT>`.

## Every request

| Condition | Response |
|---|---|
| `Host` header missing or not exactly the authority (data-model.md § Authority) | 421, empty body |
| otherwise | as below; every response has `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` |

## Routes

| Method, path | Auth | Response |
|---|---|---|
| `GET /` | none | `static/index.html`, `text/html; charset=utf-8` |
| `GET /<path>` for a file under `static/` at startup | none | that file, content type by extension |
| `GET /traces` | `Authorization: Bearer <key>` | 200 `application/json`, the trace listing (data-model.md) |
| `GET /traces`, header missing or key wrong | — | 401, empty body |
| `GET /traces`, `trace_dir` cannot be listed | key | 500, `{"error": "<exception class name>"}` |
| any other method on a route above | — | 405 |
| anything else | — | 404 |

Page files carry no trace content, so they need no key.

## Startup

| Condition | Result |
|---|---|
| `Settings` invalid | exits non-zero with the validation error (operator lists and detectors are not loaded) |
| `trace_dir` missing, inside a git working tree, not mode 0700, not owned by the user, or holding a trace file that is not 0600 and the user's | exits non-zero; the message names the directory and the reason |
| port in use | exits non-zero; uvicorn's message names the address |
| otherwise | prints `Trace viewer: http://<authority>/#key=<key>` to standard output and serves |
