# Quickstart: validating payload traces

Automated checks run in `uv run pytest` (upstream mocked with respx). The manual run below uses the real
proxy and writes real traffic to disk: use a throwaway trace directory and delete it afterwards.

## Automated scenarios

| Scenario | Spec | Test |
|---|---|---|
| Off by default writes nothing | US1 AS1, SC-001 | `tests/integration/test_tracing.py` |
| Scrubbed legs, non-streamed and streamed | US1 AS2–4 | same |
| Directory inside a git tree, wrong mode or owner fail startup | US1 AS6, FR-009, FR-010 | `tests/unit/test_file_tap.py` |
| Retention sweep | US1 AS7 | same |
| Write failure leaves requests succeeding | FR-013, SC-006 | `tests/integration/test_tracing.py` |
| Same bytes forwarded and returned, tracing on and off | SC-004 | same |
| Raw leg without opt-in fails startup | US2 AS1, SC-003 | `tests/unit/test_config.py` |
| Four legs in order; rejected request records `client_in` only | US2 AS2–3 | `tests/integration/test_tracing.py` |
| Latency with all legs on | SC-005 | `uv run pytest -m benchmark tests/benchmark/test_latency.py` |

## Manual run

```bash
export PII_PROXY_TRACE_DIR=$(mktemp -d)/traces PII_PROXY_TRACE_LEGS=upstream_out,upstream_in
uv run python -m llm_pii_proxy        # expect the startup warning naming both legs and the directory
# use Claude Code through the proxy, then:
jq -c '{leg, request_id, status, complete}' "$PII_PROXY_TRACE_DIR"/*.jsonl | head
stat -c '%a %n' "$PII_PROXY_TRACE_DIR" "$PII_PROXY_TRACE_DIR"/*.jsonl   # 700 and 600 (macOS: stat -f '%Lp %N')
```

Expected: one `upstream_out` and one `upstream_in` per request, sharing a `request_id`, with placeholders
where the conversation had real values. Settings are in [contracts/configuration.md](contracts/configuration.md).

Delete the directory when done.
