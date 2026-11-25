// Reads the viewer server's trace listing from stdin the way the page does and prints what it makes of
// it as JSON, so a Python test can check the viewer against files the proxy's own writer produced.
import { readFileSync } from "node:fs";
import { comparisons, groupRequests, parseTraceFile, payloadOf } from "../../src/llm_pii_proxy/viewer/static/traces.js";

const listing = JSON.parse(readFileSync(0, "utf8"));
// As the page does: an entry the server could not read has an error and no text.
const unreadable = listing.files.filter((f) => f.error).map(({ name, error }) => ({ name, error }));
const files = listing.files
  .filter((f) => !f.error)
  .map(({ name, text }) => ({ name, ...parseTraceFile(name, text) }));
const requests = groupRequests(files.flatMap((f) => f.records)).map((request) => ({
  requestId: request.requestId,
  sessionId: request.sessionId,
  route: request.route,
  comparisons: comparisons(request).map(({ name, left, right, shownAs }) => ({
    name,
    shownAs,
    sides: [left, right].map((s) => ({
      leg: s.leg,
      state: s.state,
      status: s.record?.status,
      complete: s.complete,
      file: s.record?.file,
      payload: s.record ? payloadOf(s.record) : undefined,
    })),
  })),
}));
const invalid = files.filter((f) => f.invalidLines).map(({ name, invalidLines }) => ({ name, invalidLines }));
process.stdout.write(JSON.stringify({ requests, invalid, unreadable }));
