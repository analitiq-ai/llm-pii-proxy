"""Leak and thinking report over a recorder capture: `analyze.py <capture dir> [--username NAME]`.

Per request: planted values found, by JSON region (`system`, `messages`, other top-level key),
the thinking blocks replayed in history and returned, and the response status. Exits 1 if any
planted value or the username was sent, or any response was not 200. Prints value indexes, never
the values.
"""

import argparse
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path

PLANTED = Path(__file__).parents[2] / "tests" / "fixtures" / "pii-project.planted.txt"
THINKING = ("thinking", "redacted_thinking")
THINKING_BLOCK = re.compile(r'"type":\s*"(?:redacted_)?thinking"')


def strings(node: object, path: str = "$") -> Iterator[tuple[str, str]]:
    if isinstance(node, dict):
        for k, v in node.items():
            yield from strings(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from strings(v, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


def region(path: str) -> str:
    return path.removeprefix("$.").split(".")[0].split("[")[0]


def thinking_in_history(body: dict) -> int:
    return sum(
        1
        for m in body.get("messages", [])
        if isinstance(m, dict) and isinstance(m.get("content"), list)
        for b in m["content"]
        if isinstance(b, dict) and b.get("type") in THINKING
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--username", help="OS username that must not reach `system` (FR-018)")
    args = parser.parse_args()
    planted = [line for line in PLANTED.read_text().splitlines() if line]
    watched = {f"planted {i}": v for i, v in enumerate(planted)}
    if args.username:
        watched["username"] = args.username

    sent: dict[str, set[str]] = {}
    not_ok = 0
    for req in sorted(args.capture.glob("*.req")):
        meta = json.loads(req.with_suffix(".meta.json").read_text())
        raw = req.read_bytes()
        body = json.loads(raw) if raw.strip() else {}
        hits: dict[str, set[str]] = {}
        for path, text in strings(body):
            for label, value in watched.items():
                if value in text:
                    hits.setdefault(label, set()).add(region(path))
        for label, regions in hits.items():
            sent.setdefault(label, set()).update(regions)
        response = req.with_suffix(".resp").read_bytes().decode(errors="replace")
        returned = len(THINKING_BLOCK.findall(response))
        not_ok += meta["status"] != 200
        print(
            f"{req.stem} {meta['method']} {meta['path']} status={meta['status']} "
            f"thinking_in_history={thinking_in_history(body)} thinking_returned={returned} "
            f"sent={ {label: sorted(r) for label, r in hits.items()} }"
        )
    print(
        f"\nSUMMARY: {len(sent)} of {len(watched)} watched values sent; {not_ok} non-200 responses"
    )
    for label, regions in sorted(sent.items()):
        print(f"  SENT {label} in {', '.join(sorted(regions))}")
    sys.exit(1 if sent or not_ok else 0)


if __name__ == "__main__":
    main()
