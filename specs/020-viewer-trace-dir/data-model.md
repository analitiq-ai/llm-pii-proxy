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

`authority(host, port)`: the host and port as a browser serialises them in a URL (WHATWG URL
host serialisation), built from `ipaddress.ip_address(host)`:

- IPv4: dotted form (`127.0.0.1`);
- IPv6: in brackets, the eight groups in lowercase hex with the longest run of zero groups compressed
  (RFC 5952), an IPv4-mapped tail written in hex, not dotted (`[::1]`, `[::ffff:7f00:1]`); Python's
  own `str()` keeps the dotted tail, so it is not used for IPv6;
- `:<port>` appended unless the port is 80, which a browser leaves out of both the URL and `Host`.

Defined once. The Host check, the printed address and the contract's served-on line all use it;
nothing joins `settings.host` and `viewer_port` directly.

## Access key

A `secrets.token_urlsafe(32)` string, held in memory by the running viewer only. Never logged, never
written to disk; a restart makes a new one.

## Trace listing (response of `GET /traces`)

```json
{"files": [{"name": "abc.jsonl", "text": "<file contents>"}, {"name": "gone.jsonl", "error": "FileNotFoundError"}]}
```

- Members: every entry directly in `trace_dir` whose name ends in `.jsonl`, found with `os.scandir`,
  sorted by name. Nothing in subdirectories; no other names.
- Each member is opened with `O_RDONLY | O_NOFOLLOW | O_NONBLOCK` (so a FIFO cannot block the
  open) and checked with `fstat` to be a regular file.
  It gets exactly one of:
  - `text`: its contents decoded as UTF-8 with `errors="replace"`, so a line cut inside a character
    becomes one invalid line, as the browser's read made it before;
  - `error`: the exception class name of any `OSError` while opening or reading it
    (`FileNotFoundError` when removed since the listing, `OSError` for a symlink, which `O_NOFOLLOW`
    refuses), or `NotARegularFile` for a directory or other non-regular entry. Never a message,
    which could carry a path or content.
- If `os.scandir(trace_dir)` or its iteration raises `OSError` (directory removed or made unreadable
  after startup), the response is 500 with body `{"error": "<exception class name>"}`, never an empty
  listing. Only that `OSError` is turned into this body; any other exception propagates to the
  server's own error handling, so a bug is not reported as an unreadable directory.

The page parses each `text` with the existing `parseTraceFile`; an `error` entry is reported as
"unreadable", as a failed browser read is today; a 500 whose JSON body has a string `error`
is shown as an unreadable directory; any other failure (rejected fetch, other status, body that does
not parse) is shown as a failed viewer request.
