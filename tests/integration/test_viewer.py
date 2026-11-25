"""The trace viewer under `viewer/`: its vendored component, its Node tests, and its fit to the
format the proxy writes."""

import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from llm_pii_proxy.tracing.file_tap import FileTap
from llm_pii_proxy.tracing.record import Leg, TraceRecord

ROOT = Path(__file__).parents[2]
VENDOR = ROOT / "viewer" / "vendor" / "json-diff-viewer"
PINNED_FILES = {"viewer.js", "diff.js", "styles.js", "LICENSE"}


def test_vendored_component_matches_its_pins():
    lines = (VENDOR / "VERSION").read_text().splitlines()
    assert "version 0.6.4" in lines
    assert any(re.fullmatch(r"integrity sha512-[A-Za-z0-9+/]+=*", line) for line in lines)
    pins = dict(
        (m[2], m[1]) for line in lines if (m := re.fullmatch(r"([0-9a-f]{64})  (\S+)", line))
    )
    assert set(pins) == PINNED_FILES
    for name, digest in pins.items():
        result = subprocess.run(
            [ROOT / "scripts" / "sha256-matches.sh", VENDOR / name, digest],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 1, f"{name} does not match its pinned sha256"
        assert result.returncode == 0, f"{name} could not be hashed: {result.stderr}"


def test_node_tests_pass():
    """Runs the viewer's own tests under Node. A glob matching no file passes with zero tests, so
    the count is checked too. Missing or old Node fails the run: the viewer is untested without
    it."""
    found = shutil.which("node")
    assert found, "node is not on PATH; the viewer tests need Node 22 or later"
    version = subprocess.run([found, "--version"], capture_output=True, text=True).stdout
    assert int(version.lstrip("v").split(".")[0]) >= 22, f"node {version.strip()} is older than 22"
    result = subprocess.run(
        [found, "--test", "tests/viewer/*.test.js"], cwd=ROOT, capture_output=True, text=True
    )
    ran = re.search(r"^# tests (\d+)$", result.stdout, re.MULTILINE)
    assert ran and int(ran[1]) > 0, f"no viewer tests ran:\n{result.stdout}{result.stderr}"
    assert result.returncode == 0, result.stdout + result.stderr


def _summarize(directory: Path) -> dict:
    result = subprocess.run(
        ["node", ROOT / "tests" / "viewer" / "summarize.js", directory],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_viewer_reads_what_the_proxy_writes(tmp_path):
    """The viewer pairs and groups records written by FileTap itself, across the file per session,
    the no-session file and a hashed session file, and reports a line that is not a record."""
    directory = tmp_path / "traces"
    tap = FileTap.open(directory, ttl_s=3600, uid=os.getuid())
    moment = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)

    def write(session_id, request_id, leg, payload, complete=True, seconds=0):
        tap.record(
            TraceRecord(
                time=moment + timedelta(seconds=seconds),
                session_id=session_id,
                request_id=request_id,
                leg=leg,
                route="/v1/messages",
                status=200,
                complete=complete,
                payload=payload,
            )
        )

    for leg in Leg:
        write(
            "s-1",
            "full",
            leg,
            {"leg": leg.value, "text": "ünïcode"},
            complete=leg != Leg.CLIENT_OUT,
        )
    write(None, "anonymous", Leg.UPSTREAM_OUT, "not json", seconds=1)
    write("has space", "hashed", Leg.UPSTREAM_IN, [1, None], seconds=2)
    with (directory / "s-1.jsonl").open("a") as trace_file:
        trace_file.write("cut short\n")

    summary = _summarize(directory)

    assert [r["requestId"] for r in summary["requests"]] == ["hashed", "anonymous", "full"]
    hashed, anonymous, full = summary["requests"]
    assert hashed["sessionId"] == "has space"
    assert anonymous["sessionId"] is None
    sent, received = full["comparisons"]
    assert [sent["shownAs"], received["shownAs"]] == ["diff", "diff"]
    assert sent["sides"][0] == {
        "leg": "client_in",
        "state": "recorded",
        "complete": True,
        "file": "s-1.jsonl",
        "payload": {"leg": "client_in", "text": "ünïcode"},
    }
    assert received["sides"][1]["complete"] is False
    assert anonymous["comparisons"][0]["sides"][1]["file"] == "_no-session.jsonl"
    assert anonymous["comparisons"][0]["sides"][1]["payload"] == "not json"
    assert [c["shownAs"] for c in anonymous["comparisons"]] == ["single", "none"]
    assert hashed["comparisons"][1]["sides"][0]["payload"] == [1, None]
    assert summary["invalid"] == [{"name": "s-1.jsonl", "invalidLines": 1}]
