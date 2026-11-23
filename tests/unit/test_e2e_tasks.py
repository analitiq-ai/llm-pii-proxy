"""The SC-002 / SC-008 task set and its grader (scripts/e2e) are consistent before any real run."""

import importlib.util
import shutil
from pathlib import Path

import pytest
import yaml

from tests.conftest import FIXTURES

E2E = Path(__file__).parents[2] / "scripts" / "e2e"
PROJECT = FIXTURES / "pii-project"
PLANTED = (FIXTURES / "pii-project.planted.txt").read_text().splitlines()
TASKS = yaml.safe_load((E2E / "tasks.yaml").read_text())["tasks"]

_spec = importlib.util.spec_from_file_location("e2e_check", E2E / "check.py")
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


def workdir(tmp_path, files=None):
    work = tmp_path / "work"
    shutil.copytree(PROJECT, work)
    for name, content in (files or {}).items():
        (work / name).write_text(content)
    return work


def test_task_set_shape():
    ids = [t["id"] for t in TASKS]
    assert len(ids) >= 20 and len(set(ids)) == len(ids)
    assert all(t.get("answer") or t.get("files") for t in TASKS)
    assert all(v in PLANTED for t in TASKS for v in t.get("changes", []))
    # SC-008 needs tasks that reuse a value in an edit, not only report one.
    assert any(set(t.get("files", {})) and not t.get("changes") for t in TASKS)


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t["id"])
def test_intended_outcome_passes(tmp_path, task):
    work = workdir(tmp_path, task.get("files"))
    assert check.failures(task, PROJECT, work, " ".join(task.get("answer", []))) == []


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t["id"])
def test_doing_nothing_fails(tmp_path, task):
    assert check.failures(task, PROJECT, workdir(tmp_path), "") != []


def test_mangled_planted_value_fails_sc002(tmp_path):
    task = next(t for t in TASKS if t["id"] == "log-level-debug")
    mangled = task["files"][".env"].replace("AKIAZ3MHN4QWRT6YB5UX", "PII_SECRET_0001")
    failed = check.failures(task, PROJECT, workdir(tmp_path, {".env": mangled}), "")
    assert f"SC-002: planted value {PLANTED.index('AKIAZ3MHN4QWRT6YB5UX')} lost from .env" in failed
