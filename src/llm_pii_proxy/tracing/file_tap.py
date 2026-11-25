"""Writes trace records as JSON lines, one file per session, into an owner-only directory."""

import asyncio
import contextlib
import hashlib
import logging
import os
import re
import stat
import time
from pathlib import Path
from typing import Protocol

from llm_pii_proxy.tracing.record import TraceRecord

log = logging.getLogger(__name__)

_DIR_MODE = 0o700
_FILE_MODE = 0o600
# The session id comes from a request header: anything else is hashed before it names a file.
_SAFE_SESSION_ID = re.compile(r"[A-Za-z0-9-]{1,128}")
# "_" is outside the safe set and the hex alphabet: neither a session id nor its hash names it.
_NO_SESSION_FILE = "_no-session.jsonl"
_OPEN_FLAGS = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC


class TraceDirError(Exception):
    """The trace directory is unsafe to hold payloads; startup must stop."""


class FileTap:
    def __init__(self, directory: Path, ttl_s: float) -> None:
        self._directory = directory
        self._ttl_s = ttl_s

    @classmethod
    def open(cls, directory: Path, ttl_s: float, uid: int) -> "FileTap":
        """Refuses a directory inside a git working tree, creates it if missing, then refuses it
        unless it is mode 0700 and every trace file in it 0600, all owned by `uid`."""
        _refuse_git_tree(directory)
        if not os.path.lexists(directory):
            directory.parent.mkdir(parents=True, exist_ok=True)
            directory.mkdir(mode=_DIR_MODE)
            directory.chmod(_DIR_MODE)
        _check_owned(directory, stat.S_ISDIR, "directory", _DIR_MODE, uid)
        for trace_file in directory.glob("*.jsonl"):
            # A file removed since the listing has no mode or owner left to check.
            with contextlib.suppress(FileNotFoundError):
                _check_owned(trace_file, stat.S_ISREG, "file", _FILE_MODE, uid)
        return cls(directory, ttl_s)

    def record(self, record: TraceRecord) -> None:
        line = record.to_line()
        fd = os.open(self._path_for(record.session_id), _OPEN_FLAGS, _FILE_MODE)
        try:
            written = os.write(fd, line)
        finally:
            os.close(fd)
        if written != len(line):
            raise OSError(f"short write: {written} of {len(line)} bytes")

    def sweep(self, now: float) -> None:
        """Deletes expired trace files. A file the OS refuses is logged and skipped, so it can't
        keep the rest; the sweep still fails once the loop is done."""
        failed = 0
        for trace_file in self._directory.glob("*.jsonl"):
            try:
                if now - os.lstat(trace_file).st_mtime > self._ttl_s:
                    trace_file.unlink(missing_ok=True)
            except FileNotFoundError:
                continue  # removed since the listing: nothing left to sweep
            except OSError as e:
                failed += 1
                log.warning("trace file %s not swept: %s", trace_file, type(e).__name__)
        if failed:
            raise OSError(f"{failed} trace file(s) could not be swept")

    def _path_for(self, session_id: str | None) -> Path:
        if session_id is None:
            return self._directory / _NO_SESSION_FILE
        if not _SAFE_SESSION_ID.fullmatch(session_id):
            session_id = hashlib.sha256(session_id.encode()).hexdigest()
        return self._directory / f"{session_id}.jsonl"


class _Sweepable(Protocol):
    def sweep(self, now: float) -> None: ...


async def sweep_periodically(tap: _Sweepable, interval_s: float) -> None:
    while True:
        await asyncio.sleep(interval_s)
        try:
            tap.sweep(time.time())
        except Exception as e:  # noqa: BLE001 - one failed sweep must not end retention for the run
            log.warning("trace retention sweep failed: %s", type(e).__name__)


def _refuse_git_tree(directory: Path) -> None:
    resolved = directory.resolve()
    for candidate in (resolved, *resolved.parents):
        if os.path.lexists(candidate / ".git"):
            raise TraceDirError(
                f"trace directory {directory} is inside a git working tree ({candidate}): "
                "traces could be committed"
            )


def _check_owned(path: Path, is_kind, kind: str, required_mode: int, uid: int) -> None:
    st = os.lstat(path)
    if not is_kind(st.st_mode):
        raise TraceDirError(f"trace {kind} {path} is not a {kind}")
    actual = stat.S_IMODE(st.st_mode)
    if actual != required_mode:
        raise TraceDirError(
            f"trace {kind} {path} has mode {actual:04o}, must be {required_mode:04o}"
        )
    if st.st_uid != uid:
        raise TraceDirError(f"trace {kind} {path} is owned by uid {st.st_uid}, must be {uid}")
