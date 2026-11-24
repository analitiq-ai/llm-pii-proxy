"""Grade one task run.

    check.py <tasks.yaml> <task id> <fixture dir> <work dir> <claude result json>

Prints PASS, or FAIL with the checks that failed, and exits 0 or 1. Never prints file contents or
the answer: they hold planted values.
"""

import json
import sys
from pathlib import Path

import yaml

PLANTED = Path(__file__).parents[2] / "tests" / "fixtures" / "pii-project.planted.txt"


def failures(task: dict, fixture: Path, work: Path, answer: str) -> list[str]:
    failed = [
        f"answer lacks expected value {i}"
        for i, s in enumerate(task.get("answer", []))
        if s not in answer
    ]
    expected = task.get("files", {})
    for name, content in expected.items():
        path = work / name
        if not path.is_file() or path.read_text() != content:
            failed.append(f"{name} differs from expected")
    originals = {p.name: p.read_text() for p in fixture.iterdir() if p.is_file()}
    for name, original in originals.items():
        after = (work / name).read_text() if (work / name).is_file() else ""
        if name not in expected and after != original:
            failed.append(f"{name} changed")
        # SC-002: a planted value the task did not mean to change is never lost or mangled.
        for i, value in enumerate(_planted()):
            if value not in task.get("changes", []) and after.count(value) < original.count(value):
                failed.append(f"SC-002: planted value {i} lost from {name}")
    return failed


def _planted() -> list[str]:
    return [line for line in PLANTED.read_text().splitlines() if line]


def main() -> None:
    tasks_path, task_id, fixture, work, result = sys.argv[1:]
    tasks = {t["id"]: t for t in yaml.safe_load(Path(tasks_path).read_text())["tasks"]}
    try:
        answer = json.loads(Path(result).read_text()).get("result") or ""
    except (OSError, json.JSONDecodeError):
        print("FAIL no parseable claude result")
        sys.exit(1)
    failed = failures(tasks[task_id], Path(fixture), Path(work), answer)
    print("PASS" if not failed else "FAIL " + "; ".join(failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
