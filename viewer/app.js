// The page: reads a picked trace directory and shows its requests and their comparisons. Every piece
// of trace text reaches the page through textContent or the diff component, which escapes it.
import "./vendor/json-diff-viewer/viewer.js";
import { comparisons, groupRequests, parseTraceFile, payloadOf, traceFiles } from "./traces.js";
import { renderTree } from "./tree.js";

const picker = document.getElementById("picker");
const fileReport = document.getElementById("file-report");
const list = document.getElementById("requests");
const detail = document.getElementById("detail");
let latestPick = 0;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function read(file, name) {
  try {
    return { name, text: await file.text() };
  } catch (error) {
    // A picked file is a snapshot: one changed or removed since the pick fails its read.
    if (error instanceof DOMException) return { name, error: error.name };
    throw error;
  }
}

picker.addEventListener("change", async () => {
  const picked = [...picker.files];
  // Cleared before any read, so picking the same directory again is a change too.
  picker.value = "";
  const pick = ++latestPick;
  fileReport.replaceChildren();
  list.replaceChildren();
  detail.replaceChildren();

  const byPath = new Map(picked.map((file) => [file.webkitRelativePath, file]));
  const results = await Promise.all(
    traceFiles([...byPath.keys()]).map(({ path, name }) => read(byPath.get(path), name)),
  );
  if (pick !== latestPick) return;

  const records = [];
  for (const result of results) {
    if (result.error) {
      fileReport.append(element("li", "unreadable", `${result.name}: unreadable (${result.error})`));
      continue;
    }
    const parsed = parseTraceFile(result.name, result.text);
    records.push(...parsed.records);
    if (parsed.invalidLines) {
      fileReport.append(
        element("li", "invalid", `${result.name}: ${parsed.invalidLines} line(s) not trace records`),
      );
    }
  }
  showList(groupRequests(records));
});

function legSummary(request) {
  const cell = element("td", "legs");
  for (const [leg, record] of Object.entries(request.legs)) {
    const tag = element("span", "leg", `${leg} ${record.status}`);
    if (!record.complete) tag.append(element("span", "flag incomplete", "incomplete"));
    cell.append(tag);
  }
  for (const leg of request.duplicateLegs) {
    cell.append(element("span", "leg flag duplicate", `${leg} duplicate`));
  }
  return cell;
}

function showList(requests) {
  if (requests.length === 0) {
    list.append(element("p", "empty", "No traced requests."));
    return;
  }
  const table = element("table");
  const head = element("tr");
  for (const title of ["Time", "Session", "Route", "Legs (status)"]) head.append(element("th", "", title));
  table.append(head);
  for (const request of requests) {
    const row = element("tr", "request");
    row.tabIndex = 0;
    row.append(
      element("td", "", request.time),
      element("td", "", request.sessionId ?? "no session"),
      element("td", "", request.route),
      legSummary(request),
    );
    const select = () => {
      table.querySelector(".selected")?.classList.remove("selected");
      row.classList.add("selected");
      showDetail(request);
    };
    row.addEventListener("click", select);
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter") select();
    });
    table.append(row);
  }
  list.append(table);
}

function sideLabel(side) {
  const label = element("div", "side-label", side.leg);
  if (side.state !== "recorded") label.append(element("span", "flag", side.state));
  if (side.complete === false) label.append(element("span", "flag incomplete", "incomplete"));
  return label;
}

function comparisonBody(comparison) {
  const { left, right, shownAs } = comparison;
  if (shownAs === "diff") {
    const { left: drawableLeft, right: drawableRight, substitutedPaths } = comparison.diffInput;
    const viewer = document.createElement("json-diff-viewer");
    viewer.setData(drawableLeft, drawableRight);
    if (substitutedPaths.length === 0) return viewer;
    const body = element("div");
    const where = substitutedPaths.join(", ");
    body.append(element("p", "flag", `Compared as JSON text, not field by field: ${where}.`), viewer);
    return body;
  }
  if (shownAs === "single") {
    const recorded = left.state === "recorded" ? left : right;
    const single = element("div", "single");
    single.append(renderTree(payloadOf(recorded.record)));
    return single;
  }
  if (shownAs === "duplicate") {
    return element("p", "flag duplicate", "More than one record for a leg: not compared.");
  }
  return element("p", "empty", "Neither leg recorded.");
}

function showDetail(request) {
  detail.replaceChildren(element("h2", "", `Request ${request.requestId}`));
  for (const comparison of comparisons(request)) {
    const section = element("section", "comparison");
    const labels = element("div", "side-labels");
    labels.append(sideLabel(comparison.left), sideLabel(comparison.right));
    section.append(element("h3", "", comparison.name), labels, comparisonBody(comparison));
    detail.append(section);
  }
}
