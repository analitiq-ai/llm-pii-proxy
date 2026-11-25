import { test } from "node:test";
import assert from "node:assert/strict";
import {
  comparisons,
  groupRequests,
  parseTraceFile,
  payloadOf,
  traceFiles,
} from "../../viewer/traces.js";

const LEGS = ["client_in", "upstream_out", "upstream_in", "client_out"];

function line(fields = {}) {
  return JSON.stringify({
    time: "2026-11-25T10:00:00.000Z",
    session_id: "s1",
    request_id: "r1",
    leg: "client_in",
    route: "/v1/messages",
    status: 200,
    complete: true,
    payload: { a: 1 },
    ...fields,
  });
}

function records(...lines) {
  return parseTraceFile("s1.jsonl", lines.join("\n")).records;
}

test("traceFiles keeps the .jsonl files directly inside the picked directory, sorted by name", () => {
  const picked = [
    "traces/b.jsonl",
    "traces/sub/c.jsonl",
    "traces/notes.txt",
    "traces/a.jsonl.bak",
    "traces/a.jsonl",
  ];
  assert.deepEqual(traceFiles(picked), [
    { path: "traces/a.jsonl", name: "a.jsonl" },
    { path: "traces/b.jsonl", name: "b.jsonl" },
  ]);
  assert.deepEqual(traceFiles([]), []);
});

test("parseTraceFile turns each valid line into a record carrying the file name and the raw line", () => {
  const first = line();
  const second = line({
    time: "2026-11-25T10:00:01.000Z",
    session_id: "s2",
    request_id: "r2",
    leg: "upstream_out",
    route: "/v1/messages/count_tokens",
    status: 502,
    complete: false,
  });
  assert.deepEqual(parseTraceFile("s1.jsonl", `${first}\n${second}`), {
    records: [
      {
        file: "s1.jsonl",
        time: "2026-11-25T10:00:00.000Z",
        sessionId: "s1",
        requestId: "r1",
        leg: "client_in",
        route: "/v1/messages",
        status: 200,
        complete: true,
        line: first,
      },
      {
        file: "s1.jsonl",
        time: "2026-11-25T10:00:01.000Z",
        sessionId: "s2",
        requestId: "r2",
        leg: "upstream_out",
        route: "/v1/messages/count_tokens",
        status: 502,
        complete: false,
        line: second,
      },
    ],
    invalidLines: 0,
  });
});

test("parseTraceFile accepts a payload of any JSON value and a null session", () => {
  const payloads = [{ a: 1 }, [1], "{", 3, false, null];
  const text = payloads.map((payload) => line({ payload, session_id: null })).join("\n");
  const { records: parsed, invalidLines } = parseTraceFile("_no-session.jsonl", text);
  assert.equal(parsed.length, payloads.length);
  assert.equal(invalidLines, 0);
  assert.ok(parsed.every((r) => r.sessionId === null));
});

test("parseTraceFile counts every line that is not a valid record, and skips empty lines", () => {
  const { payload: _, ...noPayload } = JSON.parse(line());
  const invalid = [
    line().slice(0, 30),
    "[1, 2]",
    "not json",
    JSON.stringify(noPayload),
    line({ time: "yesterday" }),
    line({ time: 5 }),
    line({ session_id: 7 }),
    line({ request_id: "" }),
    line({ request_id: 1 }),
    line({ leg: "client" }),
    line({ route: null }),
    line({ status: 200.5 }),
    line({ status: "200" }),
    line({ complete: "true" }),
  ];
  const text = ["", line(), ...invalid, "", ""].join("\n");
  const { records: parsed, invalidLines } = parseTraceFile("s1.jsonl", text);
  assert.equal(parsed.length, 1);
  assert.equal(invalidLines, invalid.length);
});

test("groupRequests makes one entry per request across files, newest first", () => {
  const a = parseTraceFile(
    "a.jsonl",
    [
      line({ request_id: "old", time: "2026-11-25T09:00:00.000Z" }),
      line({ request_id: "new", leg: "upstream_out", time: "2026-11-25T11:00:01.000Z" }),
    ].join("\n"),
  ).records;
  const b = parseTraceFile(
    "b.jsonl",
    [
      line({
        request_id: "new",
        time: "2026-11-25T11:00:00.000Z",
        session_id: "first",
        route: "/v1/messages/count_tokens",
      }),
      line({ request_id: "tie-b", time: "2026-11-25T10:00:00.000Z" }),
      line({ request_id: "tie-a", time: "2026-11-25T10:00:00.000Z" }),
    ].join("\n"),
  ).records;
  const requests = groupRequests([...a, ...b]);
  assert.deepEqual(
    requests.map((r) => r.requestId),
    ["new", "tie-a", "tie-b", "old"],
  );
  const [newest] = requests;
  assert.equal(newest.time, "2026-11-25T11:00:00.000Z");
  assert.equal(newest.sessionId, "first");
  assert.equal(newest.route, "/v1/messages/count_tokens");
  assert.deepEqual(Object.keys(newest.legs).sort(), ["client_in", "upstream_out"]);
  assert.equal(newest.legs.upstream_out.file, "a.jsonl");
  assert.deepEqual(newest.duplicateLegs, []);
});

test("groupRequests lists a leg with more than one record as duplicate, not in legs", () => {
  const [request] = groupRequests(records(line(), line(), line({ leg: "upstream_out" })));
  assert.deepEqual(request.duplicateLegs, ["client_in"]);
  assert.deepEqual(Object.keys(request.legs), ["upstream_out"]);
});

test("comparisons pairs Sent then Received and shows each by what its sides hold", () => {
  const all = groupRequests(records(...LEGS.map((leg) => line({ leg }))))[0];
  const [sent, received] = comparisons(all);
  assert.equal(sent.name, "Sent");
  assert.deepEqual([sent.left.leg, sent.right.leg], ["client_in", "upstream_out"]);
  assert.equal(received.name, "Received");
  assert.deepEqual([received.left.leg, received.right.leg], ["upstream_in", "client_out"]);
  assert.equal(sent.shownAs, "diff");
  assert.equal(sent.left.state, "recorded");
  assert.equal(sent.left.record, all.legs.client_in);
  assert.equal(sent.left.complete, true);

  const rejected = groupRequests(records(line({ status: 400 })))[0];
  const [rejectedSent, rejectedReceived] = comparisons(rejected);
  assert.equal(rejectedSent.shownAs, "single");
  assert.equal(rejectedSent.right.state, "not recorded");
  assert.equal(rejectedReceived.shownAs, "none");

  const duplicated = groupRequests(
    records(line(), line(), line({ leg: "upstream_out", complete: false })),
  )[0];
  const [dupSent] = comparisons(duplicated);
  assert.equal(dupSent.shownAs, "duplicate");
  assert.equal(dupSent.left.state, "duplicate");
  assert.equal(dupSent.right.complete, false);

  assert.equal(received.shownAs, "diff");
  const answerOnly = groupRequests(records(line({ leg: "upstream_in" })))[0];
  assert.equal(comparisons(answerOnly)[1].shownAs, "single");
  const twiceRestored = groupRequests(
    records(line({ leg: "client_out" }), line({ leg: "client_out" })),
  )[0];
  assert.equal(comparisons(twiceRestored)[1].shownAs, "duplicate");
});

test("payloadOf returns the payload parsed from the record's line, whatever JSON value it is", () => {
  for (const payload of [{ a: [1, { b: "x" }] }, [], "{", 0, true, null]) {
    const [record] = records(line({ payload }));
    assert.deepEqual(payloadOf(record), payload);
  }
});

function sentPair(left, right) {
  const request = groupRequests(
    records(line({ payload: left }), line({ leg: "upstream_out", payload: right })),
  )[0];
  return comparisons(request)[0];
}

test("comparisons shows a pair side by side, with the paths, where the diff component would hide a value", () => {
  const cases = [
    [null, { a: 1 }, ["root"]],
    [{ a: 1 }, null, ["root"]],
    [null, null, ["root"]],
    [{ content: [{ text: "x" }] }, { content: "x" }, ["root.content"]],
    [{ a: { b: [1] }, c: "x" }, { a: { b: { 0: 1 } }, c: 2 }, ["root.a.b"]],
    [{ a: 1 }, "{", ["root"]],
    [[1], { 0: 1 }, ["root"]],
    ["x", [1], ["root"]],
  ];
  for (const [left, right, paths] of cases) {
    const sent = sentPair(left, right);
    assert.equal(sent.shownAs, "side by side", JSON.stringify([left, right]));
    assert.deepEqual(sent.typeMismatches, paths, JSON.stringify([left, right]));
  }
});

test("comparisons keeps the diff where both sides render: scalars of any type and keys on one side", () => {
  const cases = [
    [{ a: null }, { a: 1 }],
    [{ a: "x" }, { a: false }],
    [{ a: { b: 1 } }, { c: [1] }],
    [[1, 2], [1]],
    ["x", 3],
  ];
  for (const [left, right] of cases) {
    const sent = sentPair(left, right);
    assert.equal(sent.shownAs, "diff", JSON.stringify([left, right]));
    assert.deepEqual(sent.typeMismatches, [], JSON.stringify([left, right]));
  }
});
