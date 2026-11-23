# Research: PII-Scrubbing Proxy

Sources were fetched on 2026-11-21. "Verified" means I executed it in this session. Everything else comes from
the cited documentation and is **not yet executed**. The spike tasks in plan.md exist to execute it.

## R0 — Spike S1 against PrivAiTe 0.8.3 (executed 2026-11-23)

**Setup**: PrivAiTe gateway (`preset: onnx`, presidio `en`, `detection_cache.enabled: true`, 127.0.0.1)
→ mitmproxy reverse recorder → api.anthropic.com. Claude Code 2.1.330, `alwaysThinkingEnabled`, 3 user turns
via `-p` / `--continue`, which made 10 upstream requests. The scratch project had 20 planted fake values: a CSV
of customers, a `.env` with an AWS key, a GitHub token and a password, and CLAUDE.md with a maintainer's name,
email and phone.

| Check | Result |
|---|---|
| Thinking-signature prefix checks | **Pass.** Up to 7 thinking blocks replayed across tool calls; 10/10 requests 200; no signature or modification errors |
| Planted values reaching Anthropic | **0 of 20** |
| CLAUDE.md | Claude Code puts it in `messages` (a system-reminder), not `system`, so it was scrubbed |
| `system` (not scanned by PrivAiTe) | **Leaked the operator's real OS username** via the memory path `/home/<user>/.claude/projects/...` |
| Files on disk after edits | Correct; real values preserved |
| Placeholder consistency | **Fail.** The same customer name became `<PERSON_3>` in one tool result and `<PERSON_10>` in another. Spans also swallowed CSV delimiters and row ids (`1,Margaret Ellsworth,` → `1<PERSON_4>`), so Claude saw broken CSV structure |
| Category accuracy | SSN → `FINANCIAL`, IP → `URL`, `https` in URLs → `<URL_1>`, 7 `DATE_TIME` hits: over-scrubbing |
| Task completion | **Fail on value-dependent tasks.** Claude declined to build a row from masked values, and to state customer 1's name or IP: it treated placeholders as irrecoverable redactions, not reversible stand-ins. It noticed the inconsistent placeholders and refused to rely on them |

**Conclusions**:
- The proxy architecture (rewrite outbound, restore inbound, never touch thinking) survives prefix checks with
  deterministic detection. This answers the R3 question for local detection.
- Gaps in PrivAiTe against this spec:
  - no `system` scrubbing (FR-002)
  - a per-request mapping, so placeholders are inconsistent across leaves (FR-004)
  - span boundaries that damage structure
  - no LLM second pass (US2)
- A finding that applies to **any** implementation: Claude must be told that placeholders are reversible and
  safe to use verbatim. Without that, value-dependent tasks fail (P3 in the critique). The notice has to be
  constant text, so it stays prefix-stable.

### R0b — Placeholder notice (FR-019), executed 2026-11-23

Same 3 turns, three runs: PrivAiTe without notice (above), PrivAiTe plus a constant notice appended to `system`
by the recorder, and Claude Code direct. 9 sub-tasks scored strictly (exact values).

| Run | Passed | Failed |
|---|---|---|
| Direct | 9/9 | none |
| PrivAiTe, no notice | 4/9 | maintainer, new row, name, IP, token prefix (all refused) |
| PrivAiTe + notice | 7/9 | new row; customer name came back as `,Margaret Ellsworth` |

- **The notice works.** All refusals stopped. Claude used placeholders in answers and tool inputs, including a
  token prefix obtained by a command whose output PrivAiTe masked and restored. Still 0 of 20 planted values
  sent, 10/10 requests 200, no signature errors.
- **New row failed**: it needed the *domain* of a masked email. Claude correctly reached for `awk` on the real
  file, but the spike's tool allowlist denied it. Interactively the user would approve; it still costs a tool
  round-trip and a permission prompt. Tasks needing **part** of a value are a known cost of whole-value
  placeholders.
- **Leading comma**: PrivAiTe's span included the CSV delimiter. The edge-trimming rule (data-model.md,
  DetectedSpan) addresses exactly this.
- **Latency, not isolated**: turns took 18/21/24 s through PrivAiTe vs 8/9/6 s direct, but the proxied runs also
  made more tool calls. Measured properly in S2 and V10.

## R1 — Interception point

- **Decision**: An HTTP gateway on loopback that Claude Code reaches through `ANTHROPIC_BASE_URL`. Routes:
  `POST /v1/messages`, `POST /v1/messages/count_tokens`, `HEAD /api/hello`, `GET /v1/models`. Everything else
  gets `404`.
- **Rationale**: These are the only paths Claude Code's gateway protocol documents on the base URL.
  `count_tokens` carries the full conversation, so it must be scrubbed too. The fast-mode check and the
  WebFetch domain check go to `api.anthropic.com` directly and carry no prompt content.
  [gateway protocol](https://code.claude.com/docs/en/llm-gateway-protocol.md)
- **Alternatives**: An HTTPS man-in-the-middle proxy (`HTTPS_PROXY` plus a local CA). It sees all traffic, but
  needs a trusted root CA and TLS interception, and gains nothing for the documented paths. Rejected.

## R2 — Authentication

- **Decision**: Forward `authorization`, `x-api-key`, `anthropic-beta` and `anthropic-version` verbatim. Never
  store or log them.
- **Rationale**: A base URL works with API keys, `ANTHROPIC_AUTH_TOKEN`, and claude.ai subscription logins. The
  subscription OAuth flow needs both `Authorization` and its `anthropic-beta` value forwarded, or it fails
  with 401. [connect](https://code.claude.com/docs/en/llm-gateway-connect.md),
  [authentication](https://code.claude.com/docs/en/authentication.md)

## R3 — Rewriting bodies vs. thinking signatures (top risk)

- **Facts**: `thinking` blocks are signed. On current models, the signature records a fingerprint of
  everything before the block: `system`, `tools`, and earlier messages. A changed prefix gets a 400 ("bound to
  a different conversation"), or the block is silently dropped. Thinking blocks in the latest assistant
  message must be returned unmodified. Claude Code's gateway guide warns that body-rewriting gateways break this
  pairing. [prefix checks](https://platform.claude.com/docs/en/build-with-claude/preserved-thinking/prefix-checks),
  [thinking](https://platform.claude.com/docs/en/build-with-claude/thinking),
  [gateway protocol](https://code.claude.com/docs/en/llm-gateway-protocol.md)
- **Decision**: The check compares against **what was sent before**, not against the user's original text.
  Rewriting is therefore safe if every re-sent leaf is rewritten to exactly the bytes sent the first time. A
  per-session leaf cache (data-model.md) guarantees this. Its key is the raw leaf text, and its value is the
  scrubbed bytes, fixed at first sight. Thinking and redacted-thinking blocks are never touched in either
  direction. `tools` isn't rewritten.
- **Consequences**:
  - A proxy restart, or session eviction, in the middle of a conversation loses the cache. Leaves are then
    re-scrubbed from scratch. With local detection only, that is usually byte-identical: spaCy and the pattern
    detectors are deterministic, and ordinals are re-issued in history order. It isn't guaranteed, and the
    confidential pass isn't deterministic. Documented behaviour: after a restart, start a new Claude Code
    conversation if requests are rejected.
  - Claude Code adds an attribution block as the first `system` block. Anthropic strips it only when it arrives
    unchanged. The quickstart sets `CLAUDE_CODE_ATTRIBUTION_HEADER=0` so the proxy never alters it.
- **Executed (R0).** A rewriting proxy (PrivAiTe) with deterministic local detection passed: 10 requests, up to
  7 replayed thinking blocks, no signature errors. Non-deterministic detection (the confidential pass) relies on
  the leaf cache and its single-flight rule, which the integration suite covers.
- **Alternatives**: Strip thinking blocks from history. Rejected, because the API rejects modified thinking
  blocks in the latest assistant turn during tool use. Disable thinking. Rejected, because it degrades Claude
  Code.

## R4 — Streaming restoration

- **Decision**: One holdback restorer, used for `text_delta.text` (raw value inserted) and
  `input_json_delta.partial_json` (JSON-string-escaped value inserted). Every other event is forwarded
  immediately. Event order is preserved.
- **Rationale**: Tokens can only appear inside JSON strings, so tool input streams through incrementally.
  Holding tool input back until `content_block_stop` would starve Claude Code's stream idle timeout during long
  file writes, and the gateway guide warns about exactly that. Event types:
  [streaming](https://platform.claude.com/docs/en/build-with-claude/streaming).
- **Alternatives**: Hold back the whole tool input and restore the parsed JSON. Rejected because of the idle
  timeout. Synthetic `ping` keepalives would only paper over that.

## R5 — Local detection (PII)

- **Decision**: Presidio Analyzer with the spaCy model `en_core_web_lg` (3.8.0, already pinned in `uv.lock`).
  Recognizers and categories:
  `PERSON`→PERSON, `LOCATION`→ADDRESS, `EMAIL_ADDRESS`→EMAIL, `PHONE_NUMBER`→PHONE, `CREDIT_CARD`→CARD,
  `IBAN_CODE`→IBAN, `US_SSN` / `UK_NHS` / other national-ID recognizers→NATIONALID, `IP_ADDRESS`→IP.
  Excluded: `DATE_TIME`, `NRP`, `URL`. They are too noisy in code and outside FR-003. The minimum score is
  configurable (`PII_PROXY_MIN_SCORE`, default 0.4). Analysis runs in a worker thread.
- **Rationale**: Presidio bundles maintained recognizers with checksum validation (Luhn, IBAN) and an NER
  backend. Over-scrubbing is acceptable under the spec as long as restoration keeps files correct.
- **Performance**: running NER on a whole Claude Code request each turn (often 100k+ tokens) would break SC-004.
  The leaf cache limits analysis to new leaves. spaCy is loaded without the dependency parser; spike S2
  confirms which other components Presidio's context enhancement needs (it reads lemmas) and drops the rest,
  then measures NER latency on 20 KB and 100 KB leaves.
- **Executed (S2, 2026-11-23)**, medians on this machine's CPU:

  | Leaf | spaCy full | spaCy tok2vec+ner | Presidio (tok2vec+ner) |
  |---|---|---|---|
  | 20 KB code | 326 ms | 187 ms | 203 ms |
  | 20 KB PII-dense CSV | 236 ms | 126 ms | 444 ms |
  | 100 KB mixed | 1444 ms | 793 ms | 2315 ms |

  - Presidio gives **identical results** (530 of 530 spans) with `tagger`, `attribute_ruler`, `lemmatizer` and
    `parser` disabled, so the pipeline keeps only `tok2vec` and `ner`.
  - Within Presidio, `PhoneRecognizer` costs about 200 ms on 20 KB of PII-dense text and `EmailRecognizer` about
    65 ms. On 100 KB, about 1.2 s is spent outside spaCy and the recognizers.
  - `en_core_web_sm` is no faster with the same trim (120 vs 123 ms on 20 KB) and finds only 29 of the 116
    names `lg` finds. `lg` stays.
  - SC-004: a 20 KB leaf fits (200–450 ms) with no margin. A 100 KB file read takes about 2.3 s.
- **Size limit**: spaCy rejects texts over `nlp.max_length` (1 000 000 chars). NER input goes through the same
  line-based chunker as the confidential pass (chunks of 100 000 chars, 200-char overlap) instead of failing
  closed on large leaves.
- **Alternatives**: GLiNER or other transformer NER. Better recall on unusual names, but slower on CPU and adds
  torch. The confidential pass covers that gap instead. `en_core_web_sm` or `md` would be faster, with weaker
  NER. Revisit if benchmarks miss SC-004.

## R6 — Session identity

- **Decision**: Key the vault and leaf cache on `x-claude-code-session-id`. A content request without it gets
  400.
- **Rationale**: The gateway protocol documents the header as "a unique identifier for the current Claude Code
  session". Subagents keep the session id and add `x-claude-code-agent-id`, so they share the vault, which is
  correct. [gateway protocol](https://code.claude.com/docs/en/llm-gateway-protocol.md)
- **Alternatives**: One vault per process. Simpler, but restart determinism gets worse because interleaved
  sessions change ordinal order. Hashing the first user message. Fragile under compaction.

## R7 — Secret detection

- **Verified** (executed 2026-11-21): `detect-secrets` `scan_line` returned `'ghp'` for a full GitHub token, the
  capture group only, and `'7E'` as a hex high-entropy secret. It identifies lines, not spans. Rejected.
- **Decision** (user, 2026-11-23, after S2): gitleaks **as a Go library** inside a long-lived child process
  that loads the rules once (contracts/secret-detector.md). Each request with cache-miss leaves sends them,
  joined by separator lines. Findings are mapped back to leaves by line, and the `Secret` field is located
  within those lines. Each finding becomes a `SECRET` span.
- **Rationale**: Gitleaks is the maintained engine for known credential formats: rule regexes, keyword
  pre-filters, entropy thresholds, allowlists. Reusing the engine avoids reimplementing it. Vendoring
  its rule file into Python would mean rewriting the keyword, entropy and allowlist semantics, which is a
  parallel implementation that drifts.
- **Executed (S2, 2026-11-23)**, gitleaks **8.30.1** (linux x64 tarball sha256
  `551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb`, verified against the release checksums):
  - Invocation: `gitleaks stdin --no-banner -l error -f json -r - --exit-code 0 --ignore-gitleaks-allow -c <ours>`.
    Fields used: `RuleID`, `StartLine`, `EndLine`, `Secret`. `StartColumn` was off by one, so the secret is
    located by searching `Secret` in the text, as planned. A private key's `Secret` spans several lines.
  - `--ignore-gitleaks-allow` is required: without it a `# gitleaks:allow` comment in the user's data hides a
    secret. Config precedence also reads `GITLEAKS_CONFIG`, `GITLEAKS_CONFIG_TOML`, a `.gitleaks.toml` and a
    `.gitleaksignore` from the working directory, so the proxy always passes `-c` and `-i` and clears those
    variables. Content must never be able to change the rules.
  - Coverage: the default rules caught 6 of 9 samples. They missed a quoted `password = "..."`, an unquoted
    `db_pass:` line and a `postgres://user:pass@host` URI. A project config with `[extend] useDefault = true`
    plus three rules (`uri-credentials`, `password-literal` for quoted literals, `password-line` for an
    unquoted value alone on its line) caught 13 of 13 across both sample sets. It flagged nothing in 70 KB of
    real Python, where a looser single password rule had flagged `password=password)` and similar code.
    Extending the config is gitleaks' own mechanism, not a re-implementation. The rules file as executed:

    ```toml
    [extend]
    useDefault = true

    [[rules]]
    id = "uri-credentials"
    description = "Password embedded in a URI (connection strings)"
    regex = '''[a-zA-Z][a-zA-Z0-9+.\-]*://[^:/?#\s@]+:([^@\s/]+)@'''
    secretGroup = 1
    keywords = ["://"]

    [[rules]]
    id = "password-literal"
    description = "Quoted string literal assigned to a password-like key (code, JSON, YAML)"
    regex = '''(?i)[a-z0-9_.\-]*(?:passw(?:or)?d|passwd|pass|pwd)["']?\s*(?:[:=]|:=|=>)\s*["']([^"'\s]{4,})["']'''
    secretGroup = 1
    keywords = ["pass", "pwd"]

    [[rules]]
    id = "password-line"
    description = "Unquoted value of a password-like key alone on its line (.env, YAML, INI)"
    regex = '''(?im)^\s*(?:export\s+)?[a-z0-9_.\-]*(?:passw(?:or)?d|passwd|pass|pwd)\s*[:=]\s*([^\s"'#]{4,})\s*$'''
    secretGroup = 1
    keywords = ["pass", "pwd"]
    ```
  - **Latency fails the target.** About 500 ms per call whatever the input size: process start is about 290 ms
    (`gitleaks version` alone), the default rules add about 170 ms to compile, and scanning 100 KB adds about
    70 ms. The floor is per process, so a fresh process per request costs about 0.5 s on every request with
    new content, which alone uses SC-004's whole budget. Relaxing SC-004 to about 1 s was rejected.
  - **Long-lived process, executed**: a ~90-line Go program on `detect.NewDetector` and `DetectString`, built
    with Go 1.27.4 (10 s build, 14 MB binary). Ready in 63 ms. Per scan: 0.8 ms for 100 B, 19 ms for 20 KB,
    144 ms for 100 KB. Findings were identical to the CLI on all 5 inputs, base64-encoded secrets included.
    `Detect` keeps no state between calls (`AddFinding` isn't on its path), so one detector serves the whole
    process lifetime. `NewDetector` leaves `MaxDecodeDepth` at 0; the CLI's default is 5, so it is set
    explicitly.
  - A secret found inside base64 is reported as its decoded value, which doesn't occur in the text. Handling is
    in contracts/secret-detector.md.
- **Pinning**: gitleaks `v8.30.1` in the sidecar's `go.mod`, with `go.sum`. Go isn't installable through uv:
  `scripts/build-secret-detector.sh` downloads Go 1.27.4 into `.tools/go` (sha256
  `7226736c2339719cda28fbf3a54d71aa63bcefe376e98cef00034268dab24840`) and builds `.tools/gitleaks-sidecar`. The
  proxy refuses to start when the process reports a different gitleaks version.
- **Alternatives**: Presidio custom PatternRecognizers with hand-written secret regexes. Rejected as
  hand-rolled. TruffleHog. A heavier binary aimed at verification against live services, and the proxy must
  never call those.

## R8 — Confidential pass (Privatemode)

- **Facts**: The Privatemode proxy exposes an OpenAI-compatible `/v1/chat/completions` on localhost, and handles
  attestation and encryption itself. All chat models "support streaming, tool calling, and structured outputs"
  through `response_format`. `json_schema` isn't named explicitly. The docs advise an explicit JSON instruction
  and `frequency_penalty` > 0 to avoid endless whitespace. Standard-tier limits: 200k prompt tokens/min and 100
  requests/min. Prices per 1M tokens: GLM-5.3-Flash €0.20 in, €0.65 out; gpt-oss-120b €0.20 in, €0.65 out
  (usage multiplier 2.0); GLM-5.3 €1.55 in, €5.74 out.
  [proxy](https://docs.privatemode.ai/api/proxy-configuration/),
  [structured outputs](https://docs.privatemode.ai/api/structured-outputs/),
  [models](https://docs.privatemode.ai/models/overview/), [rate limits](https://docs.privatemode.ai/rate-limits/),
  [pricing](https://docs.privatemode.ai/pricing/)
- **Decision**: Default model `glm-flash-latest`: cheapest tier, 1M context, multiplier 1.0. It's configurable.
  The contract is in [contracts/confidential-detection.md](contracts/confidential-detection.md). The Privatemode
  proxy holds its own key (`--api-key`).
- **Risk**: the first request of a resumed long session is entirely cache misses and can exceed 200k prompt
  tokens/min. The 429 surfaces as a 503 the client retries (FR-014). Acceptable for the P2 story, and noted in
  the quickstart.
- **Not yet executed.** Spike S3 confirms that `response_format: json_schema` is honoured by
  `glm-flash-latest` and measures latency per 24k-character chunk.

## R9 — Stack

- **Decision**: Python 3.13, `uv`. Starlette plus uvicorn for the server. httpx for upstream. `httpx-sse` to
  parse upstream SSE. pydantic-settings for configuration. Presidio and spaCy. gitleaks as a Go library in a child process (R7). Tests use
  pytest, pytest-asyncio and respx. Ruff for linting.
- **Change from scaffold**: drop `fastapi`. The proxy forwards raw JSON and needs no request models,
  validation, or OpenAI docs layer. Starlette, which FastAPI sits on, covers routing and streaming responses.
- **Rationale for `httpx-sse`**: SSE framing (multi-line `data:`, comments, retry fields) shouldn't be
  hand-parsed. Outbound SSE is re-serialised from parsed events, keeping event names and order.

## R10 — Errors and retries

- **Decision**: Proxy-originated errors use the Anthropic error shape. Deterministic failures get 4xx, which
  Claude Code doesn't retry. Transient confidential-pass failures get 502/503, which it does retry. Upstream
  errors are forwarded unmodified, because Claude Code's recovery matches their wording. Table in
  [contracts/http-interface.md](contracts/http-interface.md#proxy-originated-errors).
  [errors](https://code.claude.com/docs/en/errors.md), [API errors](https://platform.claude.com/docs/en/api/errors)

## R11 — Placeholder format

- **Decision**: `PII_<CATEGORY>_<NNNN>`, with a fixed 4-digit ordinal so a following digit can't extend it. It
  is identifier-safe and Markdown-inert, and literals are escaped.
  Rationale and rules are in [contracts/placeholder-format.md](contracts/placeholder-format.md).
- **Alternatives**: `<EMAIL_1>`. Reads as a tag in HTML, XML and JSX. Realistic fake values (a fake email
  standing in for a real one). The model reasons more naturally about them, but they can't be told apart from
  real data on the way back, so restoration becomes guesswork. Partial placeholders that keep part of a value
  visible (`PII_EMAIL_0001@example.com`): rejected by the user (2026-11-23). A domain can identify a person at a
  small company. Tasks needing part of a value are out of scope (spec Assumptions). A tool run on the real file
  would return the part unscrubbed, so the notice no longer suggests it.
