# Research: Trace viewer reads the configured trace directory

## R1. Who serves the page

- **Decision**: a separate command in the package, `python -m llm_pii_proxy.viewer`, built on Starlette
  and uvicorn.
- **Rationale**: the proxy already depends on both, so nothing is added. A separate process keeps the
  proxy's request path unchanged (FR-011) and lets the operator read traces while the proxy is down.
- **Alternatives considered**: routes on the proxy itself (couples trace reading to the process that
  carries live traffic, and only works while it runs); keeping `http.server` plus the folder picker
  (the problem the issue reports); a framework single-page app with a build step (adds a toolchain for
  no gain: the page is already one page of plain modules).

## R2. Checking the trace directory once

- **Decision**: extract the read-only checks of `FileTap.open` into
  `check_trace_dir(directory, uid)`: refuse a directory inside a git working tree; require a directory,
  mode 0700, owned by `uid`; require every `*.jsonl` in it to be a regular file, mode 0600, owned by
  `uid`. `FileTap.open` creates the directory if missing and then calls it; the viewer calls it
  directly, so a missing directory raises `TraceDirError` instead of being created.
- **Rationale**: one definition of "safe to hold payloads" (FR-003). The viewer must not create the
  directory: a viewer started with a mistyped setting would otherwise show an empty list.

## R3. Carrying the access key

- **Decision**: `secrets.token_urlsafe(32)` at each start. The printed address is
  `http://<host>:<port>/#key=<key>`; the page reads `location.hash` and sends
  `Authorization: Bearer <key>` on `GET /traces`. The server compares with `hmac.compare_digest`.
  The address is written to standard output with `print`, never through `logging`.
- **Rationale**: a fragment is never sent to the server, so the page request carries no key and
  uvicorn's access log (method, path, query) never sees it (FR-012); `Referrer-Policy: no-referrer`
  and the fragment keep it out of any Referer. A header is not logged either.
- **Alternatives considered**: query string (logged by the access log, kept in Referer); a cookie set
  by a key-carrying first request (needs a login route and cookie flags for no gain on loopback).

## R4. Address and Host check

- **Decision**: bind `PII_PROXY_HOST`, already validated as a loopback IP literal; a new
  `PII_PROXY_VIEWER_PORT`, default 8765 (the port README uses today). A middleware refuses with 421
  every request whose `Host` header is not exactly the bound authority (`127.0.0.1:8765`,
  `[::1]:8765`).
- **Rationale**: one loopback rule for both processes (FR-005). The exact Host match stops DNS
  rebinding (FR-007) as a second line behind the key: a page on another site that rebinds a name to
  127.0.0.1 sends that name as Host.
- **Alternatives considered**: Starlette's `TrustedHostMiddleware` (ignores the port, so it accepts any
  port on the same IP); a separate viewer host setting (a second loopback rule to keep in step).

## R5. Serving files without request-chosen paths

- **Decision**: at startup, list the files under `static/` into a fixed map of URL path to bytes and
  content type; a request matches a key or gets 404. `GET /traces` lists `trace_dir/*.jsonl` (regular
  files only, opened with `O_NOFOLLOW`), sorted by name, and returns
  `{"files": [{"name", "text"} | {"name", "error"}]}`.
- **Rationale**: no path from a request is ever joined to a directory (FR-008), so there is nothing to
  sanitise. The server, not the page, decides which files are trace files, so `traceFiles` leaves
  `traces.js`. One response keeps the page as simple as the picker version: it received all files at
  once then too.
- **Alternatives considered**: Starlette `StaticFiles` (resolves request paths against a directory;
  safe, but it is the request-chosen path this design avoids, and it would serve files added later);
  one route per trace file (a name parameter to validate, more requests, no benefit at this scale).

## R6. Response headers

- **Decision**: every response carries `X-Content-Type-Options: nosniff`, `Referrer-Policy:
  no-referrer` and `Cache-Control: no-store`; the page keeps its CSP meta, with `connect-src 'self'`.
- **Rationale**: `no-store` keeps trace content out of the browser's disk cache (FR-009);
  `connect-src 'self'` allows `/traces` and nothing else.
