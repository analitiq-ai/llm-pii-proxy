// Reads a trace directory the way the page does and prints what it makes of it as JSON, so a Python
// test can check the viewer against files the proxy's own writer produced.
import { readdirSync, readFileSync } from "node:fs";
import { basename, join } from "node:path";
import { comparisons, groupRequests, parseTraceFile, payloadOf, traceFiles } from "../../viewer/traces.js";

const directory = process.argv[2];
const picked = readdirSync(directory).map((name) => `${basename(directory)}/${name}`);
const files = traceFiles(picked).map(({ name }) => ({
  name,
  ...parseTraceFile(name, readFileSync(join(directory, name), "utf8")),
}));
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
      complete: s.complete,
      file: s.record?.file,
      payload: s.record ? payloadOf(s.record) : undefined,
    })),
  })),
}));
const invalid = files.filter((f) => f.invalidLines).map(({ name, invalidLines }) => ({ name, invalidLines }));
process.stdout.write(JSON.stringify({ requests, invalid }));
