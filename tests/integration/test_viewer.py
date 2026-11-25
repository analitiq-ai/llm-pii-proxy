"""The trace viewer under `viewer/`: its vendored component, its Node tests, and its fit to the
format the proxy writes."""

import re
import shutil
import subprocess
from pathlib import Path

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
    """Runs the viewer's own tests under Node. A glob matching no file passes with zero tests, so the
    count is checked too. Missing or old Node fails the run: the viewer is untested without it."""
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
