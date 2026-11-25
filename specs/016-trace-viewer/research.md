# Research: Trace viewer

## R1. Diff panels: reuse a component

- **Decision**: vendor `json-diff-viewer-component` 0.6.4 (MIT) unmodified under `viewer/vendor/`:
  its three `src/lib` files and `LICENSE`, as published on npm.
- **Rationale**: it already does the two-panel view the spec asks for: trees side by side with
  synchronised scrolling and collapsing, unchanged containers collapsed at start, and a "changed only"
  filter that is on by default (FR-003, FR-004). It has no dependencies and is 430 lines, read in full
  before choosing it: every key, value and path is passed through `escapeHtml` before it reaches
  `innerHTML` (FR-010), and it makes no network calls.
- **Alternatives considered**:
  - claude-tap, cc-viewer, llm-request-visualizer: each records one hop with its own proxy and diffs a
    turn against the previous one; none pairs two legs of one request or reads our files.
  - json-diff-kit, Monaco's diff editor: larger, and Monaco diffs text (folding lines), not a tree.
  - Writing a diff view: the reason for this choice is not to.
- **Known limits, accepted**: a changed string is marked whole (spec Assumptions). The component keys
  its expand state by `.`-joined paths, so two keys that join to the same path share it; it affects
  only which nodes are expanded.

## R2. One-sided comparisons

- **Decision**: a recorded leg whose pair is missing is shown with a small read-only tree built from
  DOM nodes and `textContent`, children rendered when a node is first opened. The missing side shows
  "not recorded".
- **Rationale**: given the same value on both sides, the component marks nothing, collapses every
  container and, with its filter on by default and no API to turn it off, shows an empty tree. Given
  an empty value on one side it marks the whole payload as added, which FR-005 forbids.
- **Alternatives considered**: patching the component (it is vendored unmodified, so it can be
  upgraded by replacing the files).

## R3. Serving the page

- **Decision**: serve `viewer/` from loopback with Python's standard library:
  `uv run python -m http.server 8765 --bind 127.0.0.1 --directory viewer`.
- **Rationale**: the page and the component are ES modules, and browsers do not load ES modules from
  `file://`. Checked with Chromium 2026-11-25: a module script on a `file://` page did not run; the
  same page served from `127.0.0.1` ran it. The server serves only the page's own files; trace data
  never crosses it (R4). It needs nothing beyond the Python the project already uses.
- **Alternatives considered**:
  - Inlining everything into one HTML file so it opens from `file://`: needs a build step that
    rewrites the vendored files, so they no longer match what was published.
  - Serving the viewer from the proxy: puts trace content behind a port that any local process can
    reach, and touches the proxy (FR-012).

## R4. Reading the traces

- **Decision**: the operator picks the trace directory with a directory file input
  (`<input type="file" webkitdirectory>`). On each pick the page clears the previous list and
  selection, keeps the `*.jsonl` files at the top level of the picked directory (one pure function,
  shared with the tests), reads each file on its own straight away, and then clears the input, so
  picking the same directory again is a new pick with new files (FR-011). A file whose read fails is
  listed in the file report with the error's name; the other files are still shown. Only the latest
  pick may change the page: a pick started while an earlier one's reads are still running makes those
  reads' results stale, so a slow pick never overwrites or mixes into a newer one.
- **Rationale**: the browser reads the files as the operator, so the 0700/0600 modes the proxy
  enforces stay as they are, and nothing is copied (FR-009). Supported by Chromium and Firefox.
  Chromium asks "Upload N files to this site?" before reading; nothing is uploaded, the page has no
  way to send data (R5). A picked file is a snapshot: checked with Chromium 2026-11-25, a picked file
  read once, then appended to, then read again failed the second read with `NotReadableError`. The
  proxy appends to the active session's file, so the page never keeps picked files to read later,
  and a read that fails is reported rather than dropped. The input lists files in subdirectories too,
  so selection keeps only those directly inside the picked directory.
- **Alternatives considered**: the File System Access directory picker (Chromium only).

## R5. Nothing leaves the machine

- **Decision**: a Content-Security-Policy meta tag:
  `default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'none';
  img-src 'self' data:; form-action 'none'; base-uri 'none'`.
- **Rationale**: `connect-src 'none'` stops fetch, XHR, WebSocket and EventSource; `default-src 'none'`
  stops loading anything else from anywhere, and `script-src 'self'` allows only the page's own files.
  Checked with Chromium 2026-11-25: under this policy a `fetch` to an outside host was refused while
  the page's own module loaded. `'unsafe-inline'` for styles is needed by the component, which puts its
  styles in a `<style>` in its shadow root and uses `style` attributes. The page uses no storage API.
- **Residual risk**: CSP cannot stop the page navigating away with data in a URL. Only script running
  in the page could do that, and payload text is never interpreted (FR-010): the component escapes it
  and the viewer's own code writes it with `textContent` only.

## R6. Holding a day of traces

- **Decision**: parse each line once, keep the record's metadata and its raw line, and drop the parsed
  payload; parse the payloads of a request again when it is selected.
- **Rationale**: Claude Code requests carry the whole conversation, so a day of traces runs to
  hundreds of MB. Keeping every parsed payload would hold several times that in the tab; keeping the
  text holds it once.
- **Alternatives considered**: extracting the metadata without a full parse (a second, partial parser
  of the format).

## R7. Testing

- **Decision**:
  - The viewer's logic (parsing lines, grouping by request, what each comparison shows) is a pure
    module with no DOM, unit-tested with Node's built-in test runner (`node --test`, Node 22). Checked:
    Node 22.18 loads the `.js` ES modules without a `package.json`.
  - One pytest test writes traces with the proxy's own `FileTap` and `TraceRecord`, runs the viewer's
    module over them under Node, and checks the result, so the viewer is pinned to the format the proxy
    writes rather than to a copy of it.
  - pytest runs the Node tests too, so `uv run pytest` stays the one command; Node 22 becomes a
    development requirement and the test fails, not skips, without it.
  - The page itself (DOM wiring) is checked by the quickstart in a browser.
- **Alternatives considered**: browser automation (Playwright): a large dependency for a thin page
  whose logic is already in the pure module.

## R8. Status in the list

- **Decision**: the list shows each recorded leg with its own status rather than one status per
  request.
- **Rationale**: client legs carry the status the client got and upstream legs the provider's (README
  § Configuration); choosing one would hide the other.
