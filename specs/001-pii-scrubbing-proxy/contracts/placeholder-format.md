# Contract: Placeholder Format

## Grammar

```
token    = "PII_" category "_" ordinal
category = 1*10UPPER-ALPHA        ; one of the Category names in data-model.md (longest: NATIONALID)
ordinal  = 4DIGIT                 ; zero-padded, 0001–9999
```

Regex for scanning responses (case-insensitive): `PII_([A-Z]{1,10})_([0-9]{4})`

Examples: `PII_EMAIL_0001`, `PII_PERSON_0012`, `PII_SECRET_0003`.

The fixed-width ordinal makes a token self-delimiting: `PII_PERSON_0001` followed by `2O3` still parses as
`PII_PERSON_0001`. A variable-width ordinal would read `PII_PERSON_12O3` as ordinal 12 and corrupt the file.
Issuing a 10 000th placeholder in one category fails the leaf closed (FR-007).

## Why this shape

- **Identifier-safe.** It is a valid identifier and survives unescaped in code, JSON strings, YAML, URLs, CSV
  and file names. The model can keep it inside a string literal or an assignment without quoting trouble.
  Angle brackets (`<EMAIL_1>`) would read as tags in HTML, XML and JSX.
- **Inert in Markdown.** No `_x_` or `__x__` pairs, so an unrestored token can't render as emphasis.
- **Self-delimiting.** Fixed width, so whatever text follows a token can't extend it.
- **Category visible.** The model can tell an email from a name and reason about the code (e.g. "validate this
  email") without seeing the value.

## Outbound (scrubbing)

1. **Escape literals first.** Any text in a leaf that matches the scanning regex (case-insensitively, as
   restoration does) is treated as a detected value of category `LITERAL` and gets its own placeholder. Real
   data that happens to look like a placeholder is therefore never confused with an issued one (FR-011).
2. **Known values next.** Occurrences of values already in the vault, including the operator's always-scrub
   values seeded at session creation (FR-018), are replaced by their tokens under the boundary rule below, so a
   value detected once is still replaced where detection misses it. The text is scanned left to right, longest
   value first at each position.

   **Boundary rule**: an occurrence is replaced only if it isn't extended by a word character (`\w`) at an
   edge where the value itself is a word character. So `Al` is replaced in `Al,` and `(Al)` but not in `Alice`
   or `Al2O3`. Edges where the value is punctuation (`@`, `.`) have no constraint.
3. **Detection last.** Detected spans whose exact text is on the never-scrub list (FR-018: the operator's
   list plus the built-in one in data-model.md) are dropped. The rest get new placeholders.

## Inbound (restoration)

- A match whose uppercased form is in the vault's `by_token` is replaced by the original value.
- A match not in the vault is left untouched and counted as `unissued` in the log (FR-010), for example
  `PII_EMAIL_0007` when only `PII_EMAIL_0001`–`0003` exist.
- Accepted variants are case variants only (`pii_email_0001`, `Pii_Email_0001`). Any other mangling, including a
  dropped leading zero (`PII_EMAIL_1`), is left as-is.
- In streamed tool input (`partial_json`), the restored value is JSON-string-escaped (`json.dumps(v)[1:-1]`)
  before it is inserted, so a value containing `"` or `\` keeps the JSON valid. In non-streamed responses,
  restoration runs on parsed JSON string values.

## Streaming holdback

One restorer serves text and tool-input deltas; only the value-escaping function differs. It keeps back the
longest suffix of its buffer that is a proper prefix of a token: a prefix of `PII_`, `PII_<1–10 letters>`,
`PII_<letters>_`, or `PII_<letters>_<0–3 digits>`, case-insensitive. A token is complete at its fourth digit and
is restored immediately, so the holdback never exceeds 18 characters. Everything before the held suffix is
restored and emitted. At `content_block_stop`, the rest is restored and flushed.
