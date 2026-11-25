"""The trace viewer under `viewer/`: its vendored component, its Node tests, and its fit to the
format the proxy writes."""

import re
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
