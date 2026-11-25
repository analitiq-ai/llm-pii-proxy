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
| tracing with only `upstream_out,upstream_in` | each comparison shows the recorded payload alone and "not recorded" on the other side; nothing marked added or removed |
| request rejected before forwarding: `curl` a body of `{` to `/v1/messages` | Sent shows `client_in`, the text `{`, with "not recorded"; Received shows "not recorded" twice |
| stream stopped mid-response (Ctrl-C in Claude Code) | the cut legs flagged incomplete in the list and the comparison |
| a garbage line appended to a trace file | the file reported with one invalid line; every other request still listed |
| more requests sent, directory picked again | new requests listed |
| browser developer tools, Network tab, whole session | no request other than the page's own files from `127.0.0.1` (SC-003) |
| a day of traces | listed in under 5 seconds (SC-004) |
