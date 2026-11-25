// A read-only tree of one JSON value, for a comparison with one side recorded: the diff component
// shows an empty tree when both sides are equal and marks everything added against an empty side.
// Text goes in with textContent only, so payload text is never parsed as markup.

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function scalar(value) {
  const type = value === null ? "null" : typeof value;
  return element("span", `val-${type}`, typeof value === "string" ? JSON.stringify(value) : String(value));
}

function container(value, label) {
  const entries = Array.isArray(value) ? value.map((v, i) => [i, v]) : Object.entries(value);
  const [open, close] = Array.isArray(value) ? ["[", "]"] : ["{", "}"];
  const details = element("details", "tree-node");
  const summary = element("summary");
  if (label !== undefined) summary.append(element("span", "key", String(label)), ": ");
  summary.append(`${open} ${entries.length} ${close}`);
  details.append(summary);
  details.addEventListener(
    "toggle",
    () => {
      for (const [key, child] of entries) details.append(entry(child, key));
    },
    { once: true },
  );
  return details;
}

function entry(value, key) {
  if (value !== null && typeof value === "object") return container(value, key);
  const row = element("div", "tree-leaf");
  row.append(element("span", "key", String(key)), ": ", scalar(value));
  return row;
}

export function renderTree(value) {
  if (value !== null && typeof value === "object") {
    const root = container(value);
    root.open = true;
    return root;
  }
  if (typeof value === "string") return element("pre", "tree-text", value);
  return scalar(value);
}
