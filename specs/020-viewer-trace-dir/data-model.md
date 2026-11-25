# Data Model: Trace viewer reads the configured trace directory

## Viewer settings (from `Settings`, env prefix `PII_PROXY_`)

| Field | Source | Rule |
|---|---|---|
| `host` | existing | loopback IP literal (existing validator) |
| `viewer_port` | new, default 8765 | 1–65535 |
| `trace_dir` | existing | absolute; at viewer start it must pass `check_trace_dir` |

All other `Settings` fields are loaded and validated as for the proxy, so a configuration the proxy
refuses is refused here too.

## Access key

A `secrets.token_urlsafe(32)` string, held in memory by the running viewer only. Never logged, never
written to disk; a restart makes a new one.

## Trace listing (response of `GET /traces`)

```json
{"files": [{"name": "abc.jsonl", "text": "<file contents>"}, {"name": "gone.jsonl", "error": "FileNotFoundError"}]}
```

- `name`: file name, no directory.
- exactly one of `text` (UTF-8 contents) or `error` (exception class name, never a message that could
  carry a path or content).
- sorted by `name`; only regular `*.jsonl` files directly in `trace_dir`.

The page parses each `text` with the existing `parseTraceFile`; an `error` entry is reported as
"unreadable", as a failed browser read is today.
