"""Local JSONL trace output (specs/004-trace-legs research R4–R7, FR-008–FR-011)."""

import asyncio
import hashlib
import json
import logging
import os
import stat
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from llm_pii_proxy.tracing import file_tap
from llm_pii_proxy.tracing.file_tap import FileTap, TraceDirError, sweep_periodically
from llm_pii_proxy.tracing.record import Leg, TraceRecord

UID = os.getuid()
DAY_S = 24 * 3600


def mode(path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def make_record(session_id: str | None = "sess-1") -> TraceRecord:
    return TraceRecord(
        time=datetime.now(UTC),
        session_id=session_id,
        request_id="r1",
        leg=Leg.UPSTREAM_OUT,
        route="/v1/messages",
        status=200,
        complete=True,
        payload={"x": 1},
    )


@pytest.fixture
def traces(tmp_path):
    directory = tmp_path / "traces"
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    return directory


# Directory checks


def test_missing_directory_created_owner_only(tmp_path):
    directory = tmp_path / "state" / "traces"
    FileTap.open(directory, DAY_S, UID)
    assert directory.is_dir()
    assert mode(directory) == 0o700


@pytest.mark.parametrize("git_entry", ["dir", "file"])
def test_directory_inside_git_tree_rejected(tmp_path, git_entry):
    repo = tmp_path / "repo"
    repo.mkdir()
    if git_entry == "dir":
        (repo / ".git").mkdir()
    else:
        (repo / ".git").write_text("gitdir: elsewhere\n")
    directory = repo / "deep" / "traces"
    with pytest.raises(TraceDirError, match=f"{directory}.*git working tree"):
        FileTap.open(directory, DAY_S, UID)
    assert not directory.exists()


@pytest.mark.parametrize("bad_mode", [0o755, 0o500])
def test_directory_with_wrong_mode_rejected(traces, bad_mode):
    traces.chmod(bad_mode)
    try:
        with pytest.raises(TraceDirError, match=f"{traces}.*{bad_mode:04o}.*0700"):
            FileTap.open(traces, DAY_S, UID)
    finally:
        traces.chmod(0o700)


def test_directory_owned_by_another_user_rejected(traces):
    with pytest.raises(TraceDirError, match=f"{traces}.*owned by uid {UID}"):
        FileTap.open(traces, DAY_S, UID + 1)


def test_trace_file_with_wrong_mode_rejected(traces):
    trace_file = traces / "sess-1.jsonl"
    trace_file.write_text("")
    trace_file.chmod(0o644)
    with pytest.raises(TraceDirError, match=f"{trace_file}.*0644.*0600"):
        FileTap.open(traces, DAY_S, UID)


def test_trace_file_owned_by_another_user_rejected(traces, monkeypatch):
    trace_file = traces / "sess-1.jsonl"
    trace_file.write_text("")
    trace_file.chmod(0o600)
    real_lstat = os.lstat

    def lstat(path, *args, **kwargs):
        result = real_lstat(path, *args, **kwargs)
        if os.fspath(path) != os.fspath(trace_file):
            return result
        fields = list(result)
        fields[stat.ST_UID] = UID + 1
        return os.stat_result(fields)

    monkeypatch.setattr(file_tap.os, "lstat", lstat)
    with pytest.raises(TraceDirError, match=f"{trace_file}.*owned by uid {UID + 1}"):
        FileTap.open(traces, DAY_S, UID)


def vanish_on_lstat(monkeypatch, gone) -> list[str]:
    """Makes `gone` disappear between the directory listing and its lstat. Returns the paths that
    vanished, so a test can show its code reached that lstat."""
    real_lstat = os.lstat
    vanished: list[str] = []

    def lstat(path, *args, **kwargs):
        if os.fspath(path) == os.fspath(gone):
            vanished.append(os.fspath(path))
            raise FileNotFoundError(path)
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(file_tap.os, "lstat", lstat)
    return vanished


def test_trace_file_removed_during_open_is_skipped(traces, monkeypatch):
    gone = traces / "gone.jsonl"
    gone.write_text("")
    gone.chmod(0o600)
    vanished = vanish_on_lstat(monkeypatch, gone)
    FileTap.open(traces, DAY_S, UID)
    assert vanished == [os.fspath(gone)]


def test_symlinked_directory_rejected(tmp_path, traces):
    link = tmp_path / "link"
    link.symlink_to(traces)
    with pytest.raises(TraceDirError, match="not a directory"):
        FileTap.open(link, DAY_S, UID)


# Writing


def test_record_appended_as_one_line_owner_only(traces):
    tap = FileTap.open(traces, DAY_S, UID)
    record = make_record()
    tap.record(record)
    tap.record(record)
    trace_file = traces / "sess-1.jsonl"
    lines = trace_file.read_bytes().splitlines(keepends=True)
    assert lines == [record.to_line()] * 2
    assert all(line.endswith(b"\n") and json.loads(line) for line in lines)
    assert mode(trace_file) == 0o600


@pytest.mark.parametrize("session_id", ["../escape", "a/b", "x" * 129, "sp ace"])
def test_unsafe_session_id_hashed(traces, session_id):
    FileTap.open(traces, DAY_S, UID).record(make_record(session_id))
    expected = hashlib.sha256(session_id.encode()).hexdigest() + ".jsonl"
    assert [p.name for p in traces.iterdir()] == [expected]


def test_symlink_at_file_path_not_followed(tmp_path, traces):
    target = tmp_path / "target"
    target.write_text("")
    tap = FileTap.open(traces, DAY_S, UID)
    (traces / "sess-1.jsonl").symlink_to(target)
    with pytest.raises(OSError):
        tap.record(make_record())
    assert target.read_text() == ""


def test_short_write_raises(traces, monkeypatch):
    tap = FileTap.open(traces, DAY_S, UID)
    real_write = os.write
    monkeypatch.setattr(file_tap.os, "write", lambda fd, data: real_write(fd, data[:-1]))
    with pytest.raises(OSError, match="short write"):
        tap.record(make_record())


# Retention


def test_sweep_deletes_only_expired_trace_files(traces):
    now = time.time()
    old, fresh, other = traces / "old.jsonl", traces / "fresh.jsonl", traces / "notes.txt"
    for path, age in ((old, DAY_S + 60), (fresh, DAY_S - 60), (other, 10 * DAY_S)):
        path.write_text("")
        path.chmod(0o600)
        os.utime(path, (now - age, now - age))
    FileTap.open(traces, DAY_S, UID).sweep(now)
    assert sorted(p.name for p in traces.iterdir()) == ["fresh.jsonl", "notes.txt"]


def test_sweep_continues_past_a_failing_entry_then_raises(traces, monkeypatch, caplog):
    now = time.time()
    expired = [traces / f"s{i}.jsonl" for i in range(3)]
    for path in expired:
        path.write_text("")
        path.chmod(0o600)
        os.utime(path, (now - 2 * DAY_S, now - 2 * DAY_S))
    tap = FileTap.open(traces, DAY_S, UID)
    real_unlink = Path.unlink
    failed = []

    def unlink(path, *args, **kwargs):
        if not failed:
            failed.append(path)
            raise PermissionError("denied")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    with caplog.at_level(logging.WARNING), pytest.raises(OSError, match="1 trace file"):
        tap.sweep(now)
    assert [p.name for p in traces.iterdir()] == [failed[0].name]
    assert failed[0].name in caplog.text and "PermissionError" in caplog.text


def test_sweep_skips_a_file_removed_after_listing(traces, monkeypatch, caplog):
    now = time.time()
    gone, expired = traces / "gone.jsonl", traces / "expired.jsonl"
    for path in (gone, expired):
        path.write_text("")
        path.chmod(0o600)
        os.utime(path, (now - 2 * DAY_S, now - 2 * DAY_S))
    tap = FileTap.open(traces, DAY_S, UID)
    vanished = vanish_on_lstat(monkeypatch, gone)
    with caplog.at_level(logging.WARNING):
        tap.sweep(now)
    assert vanished == [os.fspath(gone)]
    assert not expired.exists()
    assert "not swept" not in caplog.text


async def test_periodic_sweep_survives_a_failing_sweep(caplog):
    calls: list[float] = []

    class FlakyTap:
        def sweep(self, now: float) -> None:
            calls.append(now)
            if len(calls) == 1:
                raise PermissionError("secret path detail")

    with caplog.at_level(logging.WARNING):
        task = asyncio.create_task(sweep_periodically(FlakyTap(), 0))
        async with asyncio.timeout(1):
            while len(calls) < 2 and not task.done():
                await asyncio.sleep(0)
        assert len(calls) >= 2, "the loop ended after a failing sweep"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert "PermissionError" in caplog.text
    assert "secret path detail" not in caplog.text
