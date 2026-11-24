# Contract: Confidential Detection Pass (User Story 2)

The proxy calls a Privatemode chat model through the locally running Privatemode proxy, which handles
attestation and encryption. The Privatemode proxy is configured with its own API key (`--api-key`), so this
proxy never handles the Privatemode key.

## Input

- Only cache-miss text leaves (data-model.md, Leaf cache), **after** the outbound local stages ran. The text
  sent therefore already has every locally detected and every known value replaced (FR-012, SC-006).
- Leaves are packed into chunks of at most `PII_PROXY_CONFIDENTIAL_CHUNK_CHARS` (default 24 000), using the
  shared line-based chunker (plan.md, `detection/chunking.py`) that local NER also uses. Larger leaves are split
  on line boundaries. A single line longer than the limit is split hard at the limit, and the pieces overlap by
  200 characters so a value cut in two is still seen whole once.
- Chunks run concurrently, with at most `PII_PROXY_CONFIDENTIAL_CONCURRENCY` (default 4) in flight.

## Request

`POST {PII_PROXY_CONFIDENTIAL_URL}/v1/chat/completions` (OpenAI-compatible; default URL `http://127.0.0.1:8080`)

```json
{
  "model": "gpt-oss-120b",
  "messages": [
    {"role": "system", "content": "<detection instructions: report each description that singles out one person whole, copied verbatim, with the clauses that make it identifying but not the rest of the sentence; tokens matching PII_<CATEGORY>_<NNNN> are already redacted, text around them may still identify someone; respond with JSON only>"},
    {"role": "user", "content": "<chunk text>"}
  ],
  "response_format": {"type": "json_schema", "json_schema": {"name": "pii_findings", "strict": true, "schema": {
    "type": "object", "additionalProperties": false, "required": ["findings"],
    "properties": {"findings": {"type": "array", "items": {
      "type": "object", "additionalProperties": false, "required": ["text"],
      "properties": {"text": {"type": "string", "minLength": 1}}}}}}}},
  "temperature": 0,
  "frequency_penalty": 0.1,
  "reasoning_effort": "low",
  "max_completion_tokens": 4096
}
```

`frequency_penalty` and the explicit JSON instruction follow Privatemode's structured-output guidance, which
warns some models emit endless whitespace without them. `gpt-oss-120b` always reasons; `"low"` is its smallest
effort, and 4096 tokens leaves room for reasoning plus findings. With reasoning low, `glm-5.3` matches it on SC-005
recall and conformance at about 4× the price; with reasoning off, the GLM models replaced at most 56%
(research R8).

## Response handling

| Outcome | Action |
|---|---|
| Valid JSON matching the schema | For each `text`, every exact occurrence in the chunk becomes a `CONTEXT` span |
| A finding not found verbatim in the chunk | Dropped and counted as `confidential_unlocated`; the model paraphrased |
| A finding that overlaps an existing placeholder token | The token is cut out and the parts on either side become spans, exactly as the local stage treats detected spans that overlap fixed spans |
| Non-2xx, timeout (`PII_PROXY_CONFIDENTIAL_TIMEOUT_S`, default 30), invalid JSON, or schema mismatch | The whole Claude request fails closed (FR-014). Status `503` for transport errors and timeouts, `502` for an invalid answer. Body format in http-interface.md |

The pass's findings join the vault like any other detection (FR-013). A leaf is cached once every chunk
covering it has succeeded (data-model.md, Leaf cache). A leaf touched by a failed chunk isn't cached, so it is
sent to the pass again on the next attempt.

## Operator switch

`PII_PROXY_CONFIDENTIAL_ENABLED=true|false` (default `false`) controls the pass (FR-015). When it is false, no
connection to the Privatemode proxy is attempted.
