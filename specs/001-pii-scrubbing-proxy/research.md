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
  - **Identity line (T062, 2026-11-24, Claude Code 2.1.331).** With a subscription login, Anthropic answers
    429 "Server is temporarily limiting requests (not your usage limit)" when the first `system` block, "You
    are a Claude agent, built on Anthropic's Claude Agent SDK.", arrives altered. spaCy tags "Claude" and
    "Anthropic" as PERSON; every one of the 10 spans the local pass found in that run's system prompt was a
    product name. Direct and recorder-only requests succeeded; the same request through the proxy with those
    words never-scrubbed succeeded. **Decision (user)**: a built-in never-scrub list of Claude Code's vocabulary
    (data-model.md, Operator lists), merged with the operator's. A bare first name "Claude" is then not
    scrubbed; "Claude Dubois" still is, as never-scrub matches the whole span.
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
- **Delimited data (found in T025, 2026-11-23)**: spaCy's default tokenizer splits a comma only between two
  letters, so `1,Margaret Ellsworth,...` tokenizes as `1,Margaret` and NER found only `Ellsworth`, and missed
  `Tobias Quennell` entirely, in the fixture CSV. Adding `[,;|=]` to the tokenizer infixes (spaCy's own
  customization point) makes NER find both full names; `=` does the same for `holder="Gail Harrison"` in log
  lines (log-context name recall 2% → 98% on the T046 corpus). Presidio's pattern recognizers run on raw
  text and are unaffected.
- **Cards (found in T047)**: Presidio's `CreditCardRecognizer` pattern has no `2` prefix, so every Mastercard
  in the 2221–2720 range (issued since 2017) was missed: card recall 63%. Constructing the recognizer with
  its own pattern plus a 2-series pattern at the same weak score, Luhn-validated as before, gives 100%.
- **Postal addresses (found in T047): not covered.** The R5 mapping `LOCATION`→ADDRESS assumed spaCy's
  location entities cover postal addresses. They don't: NER tags the city and sometimes the street name
  (often as PERSON), and the house number and ZIP code are never detected. Full-coverage recall is 0% on the
  corpus against SC-003's 70%. Decision (user, 2026-11-23): scrub what can be found locally, leave the rest to
  the confidential pass. `detection/addresses.py` adds a pattern recognizer for US and UK formats: house
  number, street words and a USPS Publication 28 or UK street suffix, then optionally a locality; or city,
  state and ZIP alone; or a UK postcode alone. It is case-sensitive, since capitalisation is what separates
  `12 Main Street` from prose. Address recall 94.6%. The benchmark now resolves overlaps as the scrubber
  does before scoring, so right-category figures reflect what is actually sent.
- **Latency (T048, 2026-11-23)**: proxy time to first byte on 20 KB of corpus text (every line PII-dense)
  measured a 723 ms median. Presidio's default context enhancer reads lemmas, which are all empty once the
  lemmatizer is disabled, so it never changed a result (0 of 240 corpus records differ) yet cost about 180 ms;
  it is replaced by one that returns results unchanged. Median now about 520 ms (100 KB: 2.4 s). Of the rest,
  `PhoneRecognizer` takes 286 ms with its 8 default regions, against 77 ms for US+GB and 43 ms for US only.
  Fewer regions would stop it finding national-format numbers from the dropped countries; numbers written with
  a `+` country code are still found. Decision (user, 2026-11-23): keep all 8 regions; SC-004 is judged on
  typical content. Measured after the address recognizer was added, medians of 10 runs: 20 KB typical
  (stdlib code, a corpus record every 1600 chars) 297 ms; 20 KB all PII 478 ms (max 657 ms, recorded as the
  worst case); 100 KB typical 1.1 s.
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
  that loads the rules once (contracts/secret-detector.md). Each request with cache-miss leaves sends them as
  a list, scanned one by one, so a multi-line secret can't span two leaves. The `Secret` field is located
  within the finding's lines. Each finding becomes a `SECRET` span.
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
- **Decision**: Default model `gpt-oss-120b` (superseded `glm-flash-latest`; see the T058 results below). It's
  configurable.
  The contract is in [contracts/confidential-detection.md](contracts/confidential-detection.md). The Privatemode
  proxy holds its own key (`--api-key`).
- **Risk**: the first request of a resumed long session is entirely cache misses and can exceed 200k prompt
  tokens/min. The 429 surfaces as a 503 the client retries (FR-014). Acceptable for the P2 story, and noted in
  the quickstart.
- **Executed (S3, 2026-11-23)**: Privatemode proxy v1.64.0,
  `ghcr.io/edgelesssys/privatemode/privatemode-proxy@sha256:cf046abd3f6cbccccc69cc742533607235b756cb497f8d23d4b02f42fc223334`,
  attestation verified. 20 chunks of 24 000 characters (stdlib code, 3 `context-pii` examples each, each on a
  line with a placeholder token), contract request as written, run sequentially.
  - **`glm-flash-latest` is a reasoning model.** With the contract's request, 17 of 20 hit
    `max_completion_tokens` (2048) on reasoning tokens alone and returned empty `content`; 3 of 20 conformed.
    Median 21.7 s, max 44.5 s per chunk.
  - **Reasoning off fixes both.** `chat_template_kwargs: {"enable_thinking": false}`: 20/20 schema-conformant,
    median 1.2 s, max 1.9 s, about 100 output tokens. `reasoning_effort: "none"`: also 20/20, median 1.3 s,
    but fewer planted examples found (51 vs 56 of 60). `thinking: {"type": "disabled"}` has no effect.
  - **Spans are too wide with the contract's instruction.** Findings were whole lines, so 39 of 56 included the
    placeholder token on that line; the contract drops such findings. Asking for the shortest identifying
    phrase, never including a token: 0 findings with a token, every finding verbatim, but planted phrases fully
    covered fell to 41/60, mostly from a dropped leading "the", some from descriptions cut short.
  - A 402 (organisation quota locked until a payment card is added) came back as a plain non-2xx; the
    contract's fail-closed path covers it.
  - **Decisions (user, 2026-11-24)**: `reasoning_effort: "none"`, Privatemode's documented switch (v1.61.0+;
    `thinking: {"type": "disabled"}` applies only on `/v1/messages`, hence no effect here), over the vLLM-only
    `chat_template_kwargs`. A finding overlapping a placeholder has the token cut out, as the local stage does
    with fixed spans, rather than being dropped. The instruction asks for the shortest identifying phrase.
- **Executed (T058 recall, 2026-11-24)**: `scripts/e2e/confidential_recall.py`, full pipeline on the 32
  `context-pii` examples, each sent alone. An example counts when no content word of its phrase survives.
  - `glm-flash-latest`, contract above: 8/32 (25%). Many empty answers; the shortest-phrase findings leave
    identifying clauses behind ("Our CFO's daughter" without "the one who rows for the county").
  - `glm-flash-latest`, instruction asking for the whole description: 18/32 (56%).
  - `glm-latest` (GLM-5.3): about 0/32; it reports nothing even for "the mayor's chauffeur".
  - `gpt-oss-120b`, `reasoning_effort: "low"`, `max_completion_tokens: 4096`, whole-description instruction:
    30/32 (94%). On the S3 workload: 20/20 schema-conformant, 57/60 planted phrases inside a finding, 0
    findings in code, median 2.4 s and max 3.6 s per chunk (about 2× `glm-flash-latest`). 27 findings
    included a placeholder; the cut-out handles them.
  - **Decision (user, 2026-11-24)**: switch to `gpt-oss-120b` with the settings and instruction above. Cost:
    same per-token price, usage multiplier 2.0, and about twice the latency per chunk.
  - Re-run on the shipped code: 28/32 (88%), 30 s for 32 examples. Temperature 0 is not deterministic
    here; expect a few points of spread between runs.
  - **Fair comparison (2026-11-24)**: the GLM runs above had reasoning off, gpt-oss had it on. Same request
    for both (shipped instruction, `reasoning_effort: "low"`, 4096 tokens):

    | | `glm-5.3` | `gpt-oss-120b` |
    |---|---|---|
    | SC-005 recall, 2 runs | 31/32, 30/32 | 30/32, 30/32 |
    | S3 workload: conformant / planted found / findings in code | 20/20 / 59/60 / 0 | 20/20 / 59/60 / 0 |
    | S3 latency per chunk, median / max | 1.9 s / 3.3 s | 2.1 s / 5.0 s |
    | Price per 1M tokens in / out, after usage multiplier | €1.55 / €5.74 | €0.40 / €1.30 |

    Quality ties; GLM's earlier failure came from reasoning being off. **Decision (user, 2026-11-24)**: keep
    `gpt-oss-120b` at about a quarter of the price. `glm-5.3` is a config-only switch.

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
