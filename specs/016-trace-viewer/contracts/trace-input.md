# Contract: trace files the viewer reads

The viewer is a reader of the format the proxy already writes; it defines no format of its own.

- **Source of truth**: `TraceRecord.to_line` in `src/llm_pii_proxy/tracing/record.py`, and the file
  layout in `src/llm_pii_proxy/tracing/file_tap.py`. README § Configuration lists the fields.
- **Files**: one `.jsonl` file per session at the top level of the trace directory, named by the
  session id, or by its SHA-256 hex digest when the id is not 1–128 letters, digits and hyphens; and
  `_no-session.jsonl` for requests without one. The viewer reads every `*.jsonl` file there and
  ignores anything else; it takes the session from each record's `session_id`, never from the file
  name.
- **Lines**: one JSON object per line, one leg of one request, with `time`, `session_id`,
  `request_id`, `leg`, `route`, `status`, `complete` and `payload`. The data model's
  [Trace record](../data-model.md#trace-record) lists the rule the viewer applies to each.

## Pinned by a test

A pytest test (tasks.md T009) writes records with the proxy's own `FileTap` and `TraceRecord`, runs
the viewer's parsing and grouping over them under Node, and checks the result. It catches a change to
the writer that breaks the viewer on the records it writes: a field renamed, dropped or retyped on
those records, or a change to how files are named or where they are placed. It covers only the
cases it writes.
