# Data Model: Trace viewer reads the configured trace directory

## Viewer settings (from `Settings`, env prefix `PII_PROXY_`)

| Field | Source | Rule |
|---|---|---|
| `host` | existing | loopback IP literal (existing validator) |
| `viewer_port` | new, default 8765 | 1–65535 |
| `trace_dir` | existing | absolute; at viewer start it must pass `check_trace_dir` |

All other `Settings` fields are loaded and validated as for the proxy, so settings the proxy refuses
are refused here too. The viewer loads nothing else the proxy loads (operator lists, detectors).

## Authority

`authority(host, port)`: `127.0.0.1:8765`, or `[::1]:8765` for an IPv6 host. Defined once; both the
Host check and the printed address use it.

## Access key

A `secrets.token_urlsafe(32)` string, held in memory by the running viewer only. Never logged, never
written to disk; a restart makes a new one.

## Trace listing (response of `GET /traces`)

```json
{"files": [{"name": "abc.jsonl", "text": "<file contents>"}, {"name": "gone.jsonl", "error": "FileNotFoundError"}]}
```

- Members: every entry directly in `trace_dir` whose name ends in `.jsonl`, found with `os.scandir`,
  sorted by name. Nothing in subdirectories; no other names.
- Each member is opened with `O_RDONLY | O_NOFOLLOW` and checked with `fstat` to be a regular file.
  It gets exactly one of:
  - `text`: its contents decoded as UTF-8 with `errors="replace"`, so a line cut inside a character
    becomes one invalid line, as the browser's read made it before;
  - `error`: the exception class name of any `OSError` while opening or reading it
    (`FileNotFoundError` when removed since the listing, `OSError` for a symlink, which `O_NOFOLLOW`
    refuses), or `NotARegularFile` for a directory or other non-regular entry. Never a message,
    which could carry a path or content.
- If `trace_dir` itself cannot be listed (removed or made unreadable after startup), the response is
  500 with body `{"error": "<exception class name>"}`, never an empty listing.

The page parses each `text` with the existing `parseTraceFile`; an `error` entry is reported as
"unreadable", as a failed browser read is today; a 500 is shown as the failure it is.
