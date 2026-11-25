# Contract: viewer HTTP interface

Served by `python -m llm_pii_proxy.viewer` at `http://<authority>/`, the authority as data-model.md
§ Authority defines it for `PII_PROXY_HOST` and `PII_PROXY_VIEWER_PORT`.

## Every request

| Condition | Response |
|---|---|
| `Host` header missing or not exactly the authority | 421, empty body |
| otherwise | as below; every response has `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` |

## Routes

| Method, path | Auth | Response |
|---|---|---|
| `GET /` | none | `static/index.html`, `text/html; charset=utf-8` |
| `GET /<path>` for a file under `static/` at startup | none | that file, content type by extension |
| `GET /traces` | `Authorization: Bearer <key>` | the trace listing, 200 `application/json`, or the 500 data-model.md § Trace listing defines |
| `GET /traces`, header missing or key wrong | — | 401, empty body |
| any other method on a route above | — | 405 |
| anything else | — | 404 |

Page files carry no trace content, so they need no key.

## Startup

| Condition | Result |
|---|---|
| `Settings` invalid | exits non-zero with the validation error (operator lists and detectors are not loaded) |
| `trace_dir` fails `check_trace_dir` (research R2) | exits non-zero; the message names the directory and the reason |
| port in use | exits non-zero; uvicorn's message names the address |
| otherwise | prints `Trace viewer: http://<authority>/#key=<key>` to standard output and serves |
