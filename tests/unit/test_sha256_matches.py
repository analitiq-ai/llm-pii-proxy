"""The Go archive check: scripts/sha256-matches.sh and how build-secret-detector.sh reads its exit.

Contract: the helper exits 0 on a match, 1 on a mismatch, 2 when no digest can be computed; the
build stops on 2 instead of reporting a mismatch.
"""

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parents[2] / "scripts"
REAL_SHA256SUM = shutil.which("sha256sum")

# One stand-in per platform tool, each accepting only what the real one accepts. They hash with the
# host's sha256sum, which prints "<hex>  <file>" as all three do. macOS's BSD sha256sum has no -c.
TOOLS = {
    "gnu-sha256sum": ("sha256sum", f'exec {REAL_SHA256SUM} "$@"'),
    "bsd-sha256sum": (
        "sha256sum",
        f'case "$1" in -*) echo "bad option" >&2; exit 64;; esac\nexec {REAL_SHA256SUM} "$@"',
    ),
    "shasum": (
        "shasum",
        f'[ "$#" = 3 ] && [ "$1 $2" = "-a 256" ] || exit 2\nexec {REAL_SHA256SUM} "$3"',
    ),
}
CONTENT = b"go toolchain"
GOOD = hashlib.sha256(CONTENT).hexdigest()
BAD = hashlib.sha256(b"tampered").hexdigest()


def bin_dir(tmp_path: Path, stubs: dict[str, str], links: tuple[str, ...]) -> Path:
    """A PATH holding only these stub scripts and links to these host commands."""
    path = tmp_path / "bin"
    path.mkdir()
    for name, body in stubs.items():
        (path / name).write_text(f"#!/bin/sh\n{body}\n")
        (path / name).chmod(0o755)
    for name in links:
        (path / name).symlink_to(shutil.which(name))
    return path


def matches(path: Path, target: Path, expected: str) -> int:
    script = SCRIPTS / "sha256-matches.sh"
    return subprocess.run([script, target, expected], env={"PATH": str(path)}).returncode


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    path = tmp_path / "archive.tar.gz"
    path.write_bytes(CONTENT)
    return path


@pytest.mark.parametrize("tool", list(TOOLS))
def test_correct_archive_matches(tmp_path, archive, tool):
    name, body = TOOLS[tool]
    assert matches(bin_dir(tmp_path, {name: body}, ("bash",)), archive, GOOD) == 0


@pytest.mark.parametrize("tool", list(TOOLS))
def test_tampered_archive_is_a_mismatch(tmp_path, archive, tool):
    name, body = TOOLS[tool]
    assert matches(bin_dir(tmp_path, {name: body}, ("bash",)), archive, BAD) == 1


def test_failing_hash_tool_is_not_a_mismatch(tmp_path, archive):
    assert matches(bin_dir(tmp_path, {"sha256sum": "exit 3"}, ("bash",)), archive, GOOD) == 2


def test_unreadable_archive_is_not_a_mismatch(tmp_path):
    # A directory can't be hashed as a file whatever the uid, unlike a mode-0 file under root.
    sha256sum = {"sha256sum": TOOLS["gnu-sha256sum"][1]}
    assert matches(bin_dir(tmp_path, sha256sum, ("bash",)), tmp_path, GOOD) == 2


def test_build_stops_when_the_digest_cant_be_computed(tmp_path):
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPTS / "build-secret-detector.sh", root / "scripts")
    helper = root / "scripts" / "sha256-matches.sh"
    helper.write_text("#!/bin/sh\nexit 2\n")
    helper.chmod(0o755)
    (root / ".tools").mkdir()
    # Every supported platform's name, so the test needn't repeat the script's platform table.
    archives = [
        root / ".tools" / f"go1.27.4.{platform}.tar.gz"
        for platform in ("linux-amd64", "linux-arm64", "darwin-amd64", "darwin-arm64")
    ]
    for path in archives:
        path.write_bytes(CONTENT)
    # No curl on PATH: an attempted download fails as "curl: not found".
    path = bin_dir(tmp_path, {}, ("bash", "uname", "dirname", "mkdir"))

    result = subprocess.run(
        ["bash", root / "scripts" / "build-secret-detector.sh"],
        env={"PATH": str(path)},
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "checksum tool failed" in result.stderr
    assert "mismatch" not in result.stderr
    assert "curl" not in result.stderr
    assert all(p.read_bytes() == CONTENT for p in archives)
