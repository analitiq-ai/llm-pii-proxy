# Data model: Trace viewer

All of it lives in the browser tab, built from the files the operator picks, and is gone when the tab
closes. The input format is [contracts/trace-input.md](contracts/trace-input.md).

## Trace record

One valid line of a trace file.

| Field | From the line | Rule |
|---|---|---|
| `file` | the file's name | |
| `time` | `time` | string that parses as a date; kept as written for display |
| `sessionId` | `session_id` | string, or null for "no session" |
| `requestId` | `request_id` | non-empty string |
| `leg` | `leg` | one of `client_in`, `upstream_out`, `upstream_in`, `client_out` |
| `route` | `route` | string |
| `status` | `status` | integer |
| `complete` | `complete` | boolean |
| `line` | the raw line | kept so the payload can be parsed again when the request is selected (research R6) |

A line must be a JSON object with all eight fields and a `payload` field of any JSON value: a body
that was not JSON is recorded as its text, so a payload can be a string. Anything else,
including a line cut short, counts as invalid for its file (FR-007). Empty lines are not records and
not invalid.

## File report

Per trace file: its name and how many invalid lines it has. Shown when any file has one.

## Traced request

The records sharing a `requestId`, across files.

| Field | Rule |
|---|---|
| `requestId` | |
| `time` | the earliest record time; the list is sorted on it, newest first, ties by `requestId` |
| `sessionId`, `route` | from the earliest record |
| `legs` | leg → its record, for legs with exactly one record |
| `duplicateLegs` | legs with more than one record (FR-008) |

## Comparison

Derived from a traced request when it is selected.

| Comparison | Left | Right |
|---|---|---|
| Sent | `client_in` | `upstream_out` |
| Received | `upstream_in` | `client_out` |

Each side is `recorded` (with its record), `not recorded`, or `duplicate`. The comparison is shown as:

| Sides | Shown as |
|---|---|
| both recorded | two-panel diff of the payloads (FR-003, FR-004) |
| one recorded, one not recorded | the recorded payload alone; the other side labelled "not recorded" (FR-005) |
| neither recorded | both sides labelled "not recorded" |
| either duplicate | no payloads; the duplicate reported (FR-008) |

A recorded side with `complete` false is flagged incomplete (FR-006), in the list and in the
comparison.
