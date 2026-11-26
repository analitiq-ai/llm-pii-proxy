# Contract: HTTP Interface

The proxy is an Anthropic Messages API gateway as seen by Claude Code (`ANTHROPIC_BASE_URL`). It follows Claude
Code's gateway protocol (https://code.claude.com/docs/en/llm-gateway-protocol.md), with one deliberate deviation:
it rewrites bodies. research.md R3 explains why that is safe here.

Listens on loopback only. `PII_PROXY_HOST` must be a loopback address, and startup fails otherwise.

## Routes

Matching is on path only; query strings such as `?beta=true` are forwarded as-is.

| Method + path | Session header | Behaviour |
|---|---|---|
| `POST /v1/messages` | required | scrub → forward → restore (streamed or not) |
| `POST /v1/messages/count_tokens` | required | scrub → forward → response forwarded unchanged (it holds only counts) |
| `HEAD /api/hello` | no | forwarded unchanged (connection warming) |
| `GET /v1/models` | no | forwarded unchanged (model discovery) |
| anything else | n/a | `404 not_found_error`, **not forwarded**. Unknown endpoints may carry content the proxy can't scrub, so they fail closed |

Session header: `x-claude-code-session-id`. Missing on a content route → `400`. It selects the session's vault
and leaf cache (data-model.md).

## Request forwarding

- Upstream base: `PII_PROXY_UPSTREAM_URL` (default `https://api.anthropic.com`).
- Headers are forwarded verbatim, including `authorization`, `x-api-key`, `anthropic-version`, `anthropic-beta`
  and `x-claude-code-*`, except: hop-by-hop headers, `host`, `content-length` (recomputed), and
  `accept-encoding` (forced to `identity` so the response can be read and restored). Credentials pass through
  and are never stored or logged.
- A request body with `content-encoding` set → `415`, not forwarded. The proxy doesn't decompress.
- A body that isn't a JSON object → `400`, not forwarded.

## Which strings are scrubbed

The walk covers `system` and `messages`. Every other top-level field is forwarded untouched: `model`, `tools`,
`tool_choice`, `metadata`, `max_tokens`, `thinking`, `stream`, and so on.

Inside those two fields, every JSON string value is scrubbed, at any depth, **except**:

| Skipped | Why |
|---|---|
| any block with `type` `thinking` or `redacted_thinking`, in full | must be returned byte-for-byte or the API rejects it |
| values of keys `type`, `id`, `tool_use_id`, `name`, `signature`, `media_type`, and the whole `cache_control` object | structural identifiers, not content |
| object keys | structural |

A block whose `source` has `type` `base64`, `url` or `file` never reaches the walk: it is converted to text
or refused before it ([specs/023-media-to-text/plan.md](../../023-media-to-text/plan.md)), and the walk
raises if one does. `source` objects of type `text` (plain-text documents) are scrubbed. The walk is a skip-list, not an allow-list,
so a block type the API adds later is scrubbed by default, which is the safe direction.

A string the session recorded from one of its own earlier replies MUST be sent as recorded, and not
scrubbed again ([specs/028-replay-as-sent](../../028-replay-as-sent/spec.md)): Anthropic's own text,
placeholders included, which gives Anthropic no value it does not already have. Which strings are
recorded, and when, is data-model.md § Leaf cache; every other string is scrubbed.

## Placeholder notice (FR-019)

After scrubbing, the proxy appends one constant text block to `system` (to the end of the string if `system` is
a string; set as the only block if it's absent), on both content routes. It tells the model that `PII_*`
tokens stand for real values restored on the way back, and that it should use them verbatim wherever the real
value is needed instead of refusing or asking for it. Spike R0 showed Claude refuses value-dependent tasks
without this.

The text is a code constant, identical on every request, so it never changes the bytes of re-sent history. It
is appended after scrubbing, so its own example token is never escaped. The first `system` block is never
altered (research.md R3).

## Responses

| Upstream response | Proxy response |
|---|---|
| `2xx` JSON (`stream` false) | every string in `content[*]` restored, except `thinking` / `redacted_thinking` blocks |
| `2xx` SSE (`stream` true) | event order and framing preserved. `text_delta` and `input_json_delta` restored incrementally (data-model.md, Stream restoration state). Every other event (`ping`, `thinking_delta`, `signature_delta`, `message_*`, `error`, unknown) is forwarded immediately and unchanged |
| non-`2xx` | body and status forwarded **unmodified**: Claude Code's recovery matches the upstream wording. If a `400` body mentions `signature` or `cannot be modified` and the session was created by a request that already held assistant turns (so the proxy joined mid-conversation, e.g. after a restart), the proxy logs a hint with the session id: start a new Claude Code conversation |

Every response to a content route carries `x-pii-proxy-scrubbed: <CATEGORY>=<n>,...` (FR-017).

## Proxy-originated errors

Body shape matches the Anthropic API so Claude Code shows the message:
`{"type":"error","error":{"type":"<type>","message":"pii-proxy: <cause>"}}`

| Condition | Status | `error.type` | Claude Code retries? |
|---|---|---|---|
| Missing session header, non-JSON body | 400 | `invalid_request_error` | no |
| Local scrubbing raised (FR-007) | 400 | `invalid_request_error` | no. Deterministic, so a retry would fail the same way |
| Secret detector process crashed, timed out or answered malformed (contracts/secret-detector.md) | 503 | `api_error` | yes. A fresh process serves the retry |
| Compressed request body | 415 | `invalid_request_error` | no |
| Unknown route | 404 | `not_found_error` | no |
| Image or PDF given by URL or file id, conversion disabled, malformed block, unsupported media type, PDF that cannot be rendered, or a binary source on a block that is not an image or document (specs/023) | 400 | `invalid_request_error` | no |
| Privatemode, for the confidential pass (FR-014) or media conversion (specs/023), unreachable, timed out or answered non-2xx | 503 | `api_error`, `retry-after: 5` | yes |
| PDF rendering timed out (specs/023) | 503 | `api_error`, `retry-after: 5` | yes |
| Privatemode returned an invalid or cut-off answer (FR-014, specs/023) | 502 | `api_error` | yes. The model may answer validly next time |
| Upstream unreachable | 502 | `api_error` | yes |

No proxy error message includes request content, values or tokens.
