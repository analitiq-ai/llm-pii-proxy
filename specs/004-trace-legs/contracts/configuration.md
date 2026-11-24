# Contract: trace settings

Environment variables, read at startup like the other `PII_PROXY_*` settings.

| Variable | Default | Meaning |
|---|---|---|
| `PII_PROXY_TRACE_LEGS` | empty (tracing off) | comma-separated legs to record, e.g. `upstream_out,upstream_in`. US1 accepts `upstream_out` and `upstream_in`; US2 adds `client_in` and `client_out`. An unknown name fails startup. |
| `PII_PROXY_TRACE_DIR` | `~/.local/state/llm-pii-proxy/traces` | trace directory; checked at startup (FR-009, FR-010) |
| `PII_PROXY_TRACE_TTL_H` | `24` | retention in hours, > 0 (FR-011) |
| `PII_PROXY_TRACE_RAW` (US2) | `false` | must be `true` to select `client_in` or `client_out`; otherwise startup fails (FR-003) |

With tracing on, every startup logs a warning naming the legs and the directory, and, with a raw leg, that
real values are written to disk (FR-012).
