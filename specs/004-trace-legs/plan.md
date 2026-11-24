# Implementation Plan: Payload traces per leg

**Branch**: `4-spec` | **Date**: 2026-11-24 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/004-trace-legs/spec.md`. Story issues: US1 #8, US2 #9 (parent #4).

## Summary

Record each proxied request's payload on up to four legs, as JSON lines, to local files. Recording goes
through one `Tap` interface (FR-015): `NullTap` when tracing is off, `FileTap` for the JSONL output. A later
output (for example a self-hosted Langfuse) is another `Tap`. The proxy's handlers build one `RequestTrace` per
request, which stamps the shared fields of FR-004 and hands each enabled leg's record to the tap. Streamed
responses are assembled into one message per leg by a single assembler that watches the upstream events and
the restored events alike (research R2). Startup checks the trace directory (FR-009, FR-010) and sweeps old
files (FR-011) before the port opens.

## Technical Context

**Language/Version**: Python 3.13 (`uv`)

**Primary Dependencies**: Starlette, httpx, httpx-sse, pydantic-settings (all present); nothing new

**Storage**: JSONL files under the trace directory (FR-008)

**Testing**: pytest, pytest-asyncio, respx (upstream mocked; CLAUDE.md)

**Target Platform**: Linux and macOS, loopback-only local proxy

**Project Type**: single project, local HTTP proxy

**Performance Goals**: median added latency under 5 ms per 100 KB request with all legs on (SC-005)

**Constraints**: tracing off changes nothing (FR-001, SC-004); a trace failure never fails or delays a request
beyond the write attempt (FR-013); bodies never reach the log (FR-016)

**Scale/Scope**: one operator, one machine; files bounded by retention (default 24 h)

## Constitution Check

`.specify/memory/constitution.md` is an unfilled template. The gates are the project rules in `CLAUDE.md`
and README § Features / § Limits:

| Gate | Status |
|---|---|
| Never log bodies or the mapping; bodies only to trace files, raw legs behind a second opt-in (CLAUDE.md) | Pass: records go to the tap only; the log gets leg and failure kind (FR-013, FR-016). The mapping is never written (FR-006). |
| Fail closed; never forward unscrubbed content | Pass: tracing reads what crosses each leg and changes nothing (FR-013, SC-004). `upstream_out` records the body as forwarded. |
| Tests never call the real API (respx) | Pass: all tests mock upstream. |
| Loopback only (README) | Pass: no new listener. The later Langfuse output must be loopback-only too (research R9). |
| Mapping stays local (README) | Pass: trace files are local, owner-only, outside git trees (FR-009, FR-010). |
| Coding principles: one implementation per operation | Pass: one assembler for both streamed legs (R2); one record builder (`RequestTrace`) for all legs. |

Re-checked after Phase 1 design: no change.

## Project Structure

### Documentation (this feature)

```text
specs/004-trace-legs/
├── spec.md
├── plan.md              # this file
├── research.md          # R1–R10
├── data-model.md        # Leg, TraceRecord, RequestTrace, Tap, MessageAssembler, settings
├── quickstart.md        # validation scenarios
├── contracts/
│   ├── trace-record.md  # the JSONL line format operators read
│   └── configuration.md # PII_PROXY_TRACE_* settings
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
src/llm_pii_proxy/
├── tracing/
│   ├── __init__.py
│   ├── record.py        # Leg, TraceRecord, Tap protocol, NullTap, RequestTrace
│   ├── file_tap.py      # FileTap: directory checks, JSONL append, retention sweep
│   └── assemble.py      # MessageAssembler: SSE events -> one message
├── config.py            # + trace_legs, trace_dir, trace_ttl_h (US1); trace_raw (US2)
├── __main__.py          # build_app: directory checks, sweep, startup warning, sweep task
├── app.py               # RequestTrace per request; records at each leg
└── sse.py               # restore_stream yields events; encoding moves to the caller (R2)

tests/
├── unit/test_trace_record.py, test_file_tap.py, test_assemble.py, test_config.py (+ trace settings)
├── integration/test_tracing.py
└── benchmark/test_latency.py (+ tracing-on case, SC-005)
```

**Structure Decision**: a `tracing` package beside the existing modules, so a later output is one more file
behind `Tap` and request handling does not change (FR-015).

## Delivery

- **US1 (#8)** — scrubbed legs: settings (`upstream_out`, `upstream_in` only), `Tap`/`NullTap`/`FileTap`,
  directory checks, retention, startup warning, assembler and the `sse.py` change, wiring in `app.py`, README
  Features sentence and configuration docs.
- **US2 (#9)**, blocked by #8 — raw legs: `client_in`/`client_out` in `Leg`, `trace_raw` and its startup check
  (FR-003), records for requests rejected before forwarding (FR-014), the no-session file (FR-008), warning
  wording for real values (FR-012).

## Complexity Tracking

None.
