# Data Model: Trace viewer reads the configured trace directory

Each rule below is stated here only; the contract, research and tasks link to it.

## Viewer settings (from `Settings`, env prefix `PII_PROXY_`)

| Field | Source | Rule |
|---|---|---|
| `host` | existing | loopback IP literal (existing validator) |
| `viewer_port` | new, default 8765 | 1–65535 |
| `trace_dir` | existing | absolute; at viewer start it must pass `check_trace_dir` |

All other `Settings` fields are loaded and validated as for the proxy, so settings the proxy refuses
are refused here too. The viewer loads nothing else the proxy loads (operator lists, detectors).

## Authority

`authority(host, port)` is defined for every `host` the proxy's loopback validator accepts. It is the
host and port as a browser serialises them in a URL (WHATWG URL host serialisation), computed from
`ipaddress.ip_address(host)` with any IPv6 scope id dropped:

- IPv4: dotted form (`127.0.0.1`);
- IPv6: in brackets, the eight groups in lowercase hex with the longest run of zero groups compressed
  (RFC 5952), an IPv4-mapped tail in hex, not dotted; Python's `str()` keeps a dotted tail and the
  scope id, so it is not used for IPv6. `::1`, `0:0:0:0:0:0:0:1` and `::1%lo` give `[::1]`;
  `::ffff:127.0.0.1` gives `[::ffff:7f00:1]`;
- `:<port>` appended unless the port is 80, which a browser leaves out of both the URL and `Host`.

The Host check and the printed address both use it; nothing joins `settings.host` and `viewer_port`
directly.

## Access key

A `secrets.token_urlsafe(32)` string, held in memory by the running viewer only. Never logged, never
written to disk; a restart makes a new one.

## Trace listing (`GET /traces`)

```json
{"files": [{"name": "abc.jsonl", "text": "<file contents>"}, {"name": "gone.jsonl", "error": "FileNotFoundError"}]}
```

- **Members**: every entry directly in `trace_dir` whose name ends in `.jsonl`, found with
  `os.scandir`, sorted by name. Nothing in subdirectories; no other names.
- **Per member**: opened with `O_RDONLY | O_NOFOLLOW | O_NONBLOCK` (a FIFO cannot block the open), then
  checked with `fstat` to be a regular file. It gets exactly one of:
  - `text`: its contents decoded as UTF-8 with `errors="replace"`, so a line cut inside a character is
    one invalid line, as the browser's read made it before;
  - `error`: the exception class name of any `OSError` while opening or reading it
    (`FileNotFoundError` when removed since the listing; `OSError` for a symlink, which `O_NOFOLLOW`
    refuses), or `NotARegularFile` for a directory, FIFO or other non-regular entry. Never a message,
    which could carry a path or content.
- **Whole response**: an `OSError` raised by `os.scandir(trace_dir)` or while iterating it (directory
  removed or made unreadable after startup) gives 500 with body `{"error": "<exception class name>"}`,
  never an empty listing. No other exception is turned into that body; anything else propagates to the
  server's own error handling, so a bug is not reported as an unreadable directory.

## Page outcomes

What the page shows for each result of loading the traces. One pure function in `static/traces.js`
maps a result to its outcome.

| Result | Page shows |
|---|---|
| no `key` in `location.hash` | "Open the address the viewer printed." (no request made) |
| 200 with a body that parses as a listing | the file report (each `error` entry as "<name>: unreadable (<error>)", invalid lines per file) and the request list, as today |
| 401 | "Key refused: the viewer was restarted; open the address it printed." |
| 500 whose body is JSON with a string `error` | "Trace directory unreadable: <error>" |
| a rejected fetch, any other status, a 500 without that body, or a 200 body that does not parse as a listing | "Viewer request failed: <status, or the error's name>" |
