// What the viewer makes of trace files: which lines are records, how
// records group into requests and pair into comparisons. No DOM, so Node tests it as the page runs it.

const LEGS = ["client_in", "upstream_out", "upstream_in", "client_out"];
const PAIRS = [
  ["Sent", "client_in", "upstream_out"],
  ["Received", "upstream_in", "client_out"],
];

const RULES = {
  time: (v) => typeof v === "string" && !Number.isNaN(Date.parse(v)),
  session_id: (v) => v === null || typeof v === "string",
  request_id: (v) => typeof v === "string" && v !== "",
  leg: (v) => LEGS.includes(v),
  route: (v) => typeof v === "string",
  status: (v) => Number.isInteger(v),
  complete: (v) => typeof v === "boolean",
};

function asRecord(file, line) {
  let fields;
  try {
    fields = JSON.parse(line);
  } catch (error) {
    if (error instanceof SyntaxError) return null;
    throw error;
  }
  if (fields === null || typeof fields !== "object" || Array.isArray(fields)) return null;
  if (!Object.hasOwn(fields, "payload")) return null;
  if (!Object.entries(RULES).every(([key, valid]) => valid(fields[key]))) return null;
  return {
    file,
    time: fields.time,
    sessionId: fields.session_id,
    requestId: fields.request_id,
    leg: fields.leg,
    route: fields.route,
    status: fields.status,
    complete: fields.complete,
    line,
  };
}

export function parseTraceFile(file, text) {
  const records = [];
  let invalidLines = 0;
  for (const line of text.split("\n")) {
    if (line === "") continue;
    const record = asRecord(file, line);
    if (record) records.push(record);
    else invalidLines += 1;
  }
  return { records, invalidLines };
}

export function groupRequests(records) {
  const byId = new Map();
  for (const record of records) {
    const group = byId.get(record.requestId) ?? [];
    group.push(record);
    byId.set(record.requestId, group);
  }
  const requests = [...byId].map(([requestId, group]) => {
    const earliest = group.reduce((a, b) => (Date.parse(b.time) < Date.parse(a.time) ? b : a));
    const legs = {};
    const duplicateLegs = [];
    for (const leg of LEGS) {
      const held = group.filter((r) => r.leg === leg);
      if (held.length === 1) legs[leg] = held[0];
      else if (held.length > 1) duplicateLegs.push(leg);
    }
    return {
      requestId,
      time: earliest.time,
      sessionId: earliest.sessionId,
      route: earliest.route,
      legs,
      duplicateLegs,
    };
  });
  return requests.sort(
    (a, b) =>
      Date.parse(b.time) - Date.parse(a.time) ||
      (a.requestId < b.requestId ? -1 : a.requestId > b.requestId ? 1 : 0),
  );
}

function side(request, leg) {
  if (request.duplicateLegs.includes(leg)) return { leg, state: "duplicate" };
  const record = request.legs[leg];
  if (!record) return { leg, state: "not recorded" };
  return { leg, state: "recorded", record, complete: record.complete };
}

function shownAs(left, right) {
  const states = [left.state, right.state];
  if (states.includes("duplicate")) return "duplicate";
  const recorded = states.filter((s) => s === "recorded").length;
  return ["none", "single", "diff"][recorded];
}

function typeOf(value) {
  if (value === null) return "null";
  return Array.isArray(value) ? "array" : typeof value;
}

const isContainer = (value) => value !== null && typeof value === "object";

// The vendored diff component renders nothing for a null root, and where an array or object meets a
// value of another type it draws an empty container. Such a value goes to it as its JSON text on both
// sides instead, so the conflict shows as a changed value and the rest of the payload is still diffed.
function asDrawable(a, b, path, substituted) {
  if (typeOf(a) !== typeOf(b)) {
    if (!isContainer(a) && !isContainer(b)) return [a, b];
    substituted.push(path);
    return [JSON.stringify(a), JSON.stringify(b)];
  }
  if (!isContainer(a)) return [a, b];
  const shared = new Map();
  for (const key of Object.keys(a)) {
    if (Object.hasOwn(b, key)) shared.set(key, asDrawable(a[key], b[key], `${path}.${key}`, substituted));
  }
  // Rebuilt rather than assigned into, so a "__proto__" key in a payload stays a plain key.
  const copy = (value, sideIndex) =>
    Array.isArray(value)
      ? value.map((item, index) => (shared.has(String(index)) ? shared.get(String(index))[sideIndex] : item))
      : Object.fromEntries(
          Object.entries(value).map(([key, item]) => [key, shared.has(key) ? shared.get(key)[sideIndex] : item]),
        );
  return [copy(a, 0), copy(b, 1)];
}

function diffInput(leftRecord, rightRecord) {
  const left = payloadOf(leftRecord);
  const right = payloadOf(rightRecord);
  if (left === null || right === null) {
    return { left: JSON.stringify(left), right: JSON.stringify(right), substitutedPaths: ["root"] };
  }
  const substitutedPaths = [];
  const [drawableLeft, drawableRight] = asDrawable(left, right, "root", substitutedPaths);
  return { left: drawableLeft, right: drawableRight, substitutedPaths };
}

export function comparisons(request) {
  return PAIRS.map(([name, leftLeg, rightLeg]) => {
    const left = side(request, leftLeg);
    const right = side(request, rightLeg);
    const shown = shownAs(left, right);
    return {
      name,
      left,
      right,
      shownAs: shown,
      diffInput: shown === "diff" ? diffInput(left.record, right.record) : null,
    };
  });
}

export function payloadOf(record) {
  return JSON.parse(record.line).payload;
}
