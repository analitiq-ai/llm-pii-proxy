# Quickstart: validate the viewer on the configured trace directory

Prerequisites: `uv sync`, Node 22 or later, a trace directory with records (run the proxy with
`PII_PROXY_TRACE_LEGS=upstream_out,upstream_in` and send a request through it).

1. Automated: `uv run pytest tests/integration/test_viewer_server.py tests/integration/test_viewer.py`
   passes.
2. Start the viewer with the proxy's environment: `uv run python -m llm_pii_proxy.viewer`. It prints
   `Trace viewer: http://127.0.0.1:8765/#key=…`.
3. Open that address. The request list appears; there is no directory picker. (Spec US1 scenario 1.)
4. Send another request through the proxy, reload the page: it is listed. (Scenario 2.)
5. Open `http://127.0.0.1:8765/` without the fragment: the page says to open the printed address and
   shows no traces. `curl -i http://127.0.0.1:8765/traces` returns 401; with
   `-H 'Host: example.test:8765'` it returns 421. (Scenario 4; contracts/viewer-http.md.)
6. Stop the viewer, `chmod 755` the trace directory, start it again: it exits naming the directory and
   the mode. Restore 700. (Edge cases.)
7. Start a second viewer while the first runs: it exits non-zero and the message names the address in
   use. (Edge cases.)
8. With the page open, move the trace directory away and reload: the page says the trace directory is
   unreadable, not that there are no requests. Move it back and reload: the requests return. (Edge
   cases.)
9. With the page open, the browser's network panel shows requests only to the viewer's own address.
   (SC-004.)
