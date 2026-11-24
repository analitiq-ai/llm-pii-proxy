"""scripts/sha256-matches.sh, run with only one hashing tool on PATH, as on each supported OS."""

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "sha256-matches.sh"
REAL_SHA256SUM = shutil.which("sha256sum")

# One stand-in per platform tool, each accepting only what the real one accepts. They hash with the
# host's sha256sum, which prints "<hex>  <file>" as all three do. macOS's BSD sha256sum has no -c.
TOOLS = {
    "gnu-sha256sum": ("sha256sum", f'exec {REAL_SHA256SUM} "$@"'),
    "bsd-sha256sum": (
        "sha256sum",
        f'case "$1" in -*) echo "bad option" >&2; exit 64;; esac\nexec {REAL_SHA256SUM} "$@"',
    ),
    "broken": ("sha256sum", "exit 3"),
    "shasum": (
        "shasum",
        f'[ "$#" = 3 ] && [ "$1 $2" = "-a 256" ] || exit 2\nexec {REAL_SHA256SUM} "$3"',
    ),
}


def run(tmp_path: Path, tool: str, expected: str, readable: bool = True) -> int:
    name, body = TOOLS[tool]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / name).write_text(f"#!/bin/sh\n{body}\n")
    (bin_dir / name).chmod(0o755)
    for needed in ("bash", "cut"):
        (bin_dir / needed).symlink_to(shutil.which(needed))
    archive = tmp_path / "archive.tar.gz"
    archive.write_bytes(b"go toolchain")
    if not readable:
        archive.chmod(0)
    return subprocess.run(
        [SCRIPT, archive, expected], env={"PATH": str(bin_dir)}, check=False
    ).returncode


PLATFORM_TOOLS = [t for t in TOOLS if t != "broken"]
GOOD = hashlib.sha256(b"go toolchain").hexdigest()
BAD = hashlib.sha256(b"tampered").hexdigest()


@pytest.mark.parametrize("tool", PLATFORM_TOOLS)
def test_correct_archive_matches(tmp_path, tool):
    assert run(tmp_path, tool, GOOD) == 0


@pytest.mark.parametrize("tool", PLATFORM_TOOLS)
def test_tampered_archive_is_a_mismatch(tmp_path, tool):
    assert run(tmp_path, tool, BAD) == 1


# Exit 2, not 1: the build must not report a tool failure as a mismatch (issue #6 was one).
@pytest.mark.parametrize("tool", PLATFORM_TOOLS)
def test_unreadable_archive_is_a_tool_failure(tmp_path, tool):
    assert run(tmp_path, tool, GOOD, readable=False) == 2


def test_failing_hash_tool_is_a_tool_failure(tmp_path):
    assert run(tmp_path, "broken", GOOD) == 2
