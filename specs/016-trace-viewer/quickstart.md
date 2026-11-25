# Quickstart: Trace viewer

## Prerequisites

- The repo checked out and `uv sync` done.
- Node 22 or later, for the tests.
- Chromium or Firefox on the machine that holds the traces.

## Tests

```
uv run pytest tests/integration/test_viewer.py
```

Runs the viewer's unit tests under Node and the check against records written by the proxy's own
writer ([contracts/trace-input.md](contracts/trace-input.md)). Fails if Node is missing.

## Open the viewer

```
uv run python -m http.server 8765 --bind 127.0.0.1 --directory viewer
```

Open `http://127.0.0.1:8765/`, choose the trace directory (default
`~/.local/state/llm-pii-proxy/traces`; show hidden folders in the picker to reach it). Chromium asks
whether to upload the files: nothing is uploaded, the page can't send data (research R5).

## Check by hand

Run the proxy with tracing on and send a few requests from Claude Code, or write a directory with the
cases below. In the viewer:

| Case | Expected |
|---|---|
| request with all four legs, prompt containing an email | listed once with four legs; Sent marks the email against its placeholder; Received marks each restored placeholder |
| the same request, comparison first opened | unchanged parts collapsed; the "changed only" filter on, and turning it off shows the unchanged parts (FR-004) |
| tracing with only `upstream_out,upstream_in` | each comparison shows the recorded payload alone and "not recorded" on the other side; nothing marked added or removed |
| request rejected before forwarding: `curl` a body of `{` to `/v1/messages` | Sent shows `client_in`, the text `{`, with "not recorded"; Received shows "not recorded" twice |
| stream stopped mid-response (Ctrl-C in Claude Code) | the cut legs flagged incomplete in the list and the comparison |
| a garbage line appended to a trace file | the file reported with one invalid line; every other request still listed |
| a trace file made unreadable (`chmod 000`) | the file reported as unreadable with the error's name; every other request still listed |
| an empty directory; a directory holding only an empty `.jsonl` file | "no traced requests" |
| a line of a fully traced request duplicated in its file | the request reported with a duplicate leg; that leg's comparison not shown |
| a field added to the `upstream_out` payload by hand | Sent marks the field as added |
| a prompt containing `<img src=x onerror=alert(1)>`, in a diff and in a one-sided view | the markup shown as text in both; no dialog, no image request in the Network tab (FR-010) |
| a valid trace file copied into a subdirectory, and a `.txt` file | neither read; the subdirectory's requests not listed |
| another request sent in the same Claude Code session, directory picked again | the new request listed (its session file grew, no new file) |
| another Claude Code session started, directory picked again | its requests listed |
| browser developer tools, Network tab, whole session | no request other than the page's own files from `127.0.0.1` (SC-003) |
| browser developer tools, Application tab, after viewing requests | local storage, session storage, IndexedDB and cookies for `127.0.0.1:8765` empty (FR-009) |
| a day of traces | listed in under 5 seconds (SC-004) |
