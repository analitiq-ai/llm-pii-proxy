"""Client for the secret detector process (contracts/secret-detector.md)."""

import asyncio
import json
import re
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from llm_pii_proxy.config import PINNED_GITLEAKS_VERSION
from llm_pii_proxy.detection import DetectedSpan, Stage
from llm_pii_proxy.errors import ScrubFailed, SecretDetectorUnavailable
from llm_pii_proxy.processes import kill_process
from llm_pii_proxy.vault import Category

RULES_PATH = Path(str(files("llm_pii_proxy.detection").joinpath("gitleaks.toml")))

_STREAM_LIMIT = 64 * 1024 * 1024  # matches the detector's stdin line limit
_ENCODING_RUN = re.compile(r"[A-Za-z0-9+/=_%-]+")
_NEWLINE = re.compile(r"\n")


class SecretDetectorStartupError(RuntimeError):
    pass


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule: str
    secret: str
    start_line: int
    end_line: int
    start_column: int
    end_column: int
    tags: list[str]

    @property
    def decoded(self) -> bool:
        return any(t.startswith("decoded:") for t in self.tags)


class _Ready(BaseModel):
    ready: bool
    gitleaks: str
    rules: int


class _Response(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    findings: list[list[Finding]]


class SecretDetector:
    def __init__(
        self,
        binary: Path,
        rules: Path = RULES_PATH,
        *,
        pinned_version: str = PINNED_GITLEAKS_VERSION,
        ready_timeout: float = 5.0,
        call_timeout: float = 10.0,
    ) -> None:
        self._binary = binary
        self._rules = rules
        self._pinned_version = pinned_version
        self._ready_timeout = ready_timeout
        self.call_timeout = call_timeout
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._last_id = 0
        self.version: str | None = None

    @property
    def pid(self) -> int:
        if self._process is None:
            raise RuntimeError("secret detector not running")
        return self._process.pid

    async def start(self) -> None:
        self._process = await self._spawn()

    async def close(self) -> None:
        process, self._process = self._process, None
        if process is None or process.returncode is not None:
            return
        assert process.stdin is not None
        process.stdin.close()
        try:
            async with asyncio.timeout(2):
                await process.wait()
        except TimeoutError:
            await kill_process(process)

    async def detect(self, texts: Sequence[str]) -> list[list[DetectedSpan]]:
        if not texts:
            return []
        async with self._lock:
            findings = await self._call(texts)
        return [findings_to_spans(t, f) for t, f in zip(texts, findings, strict=True)]

    async def _spawn(self) -> asyncio.subprocess.Process:
        # Empty environment, cwd "/": no GITLEAKS_* variable or .gitleaks.toml can change the rules.
        try:
            process = await asyncio.create_subprocess_exec(
                str(self._binary),
                str(self._rules),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={},
                cwd="/",
                limit=_STREAM_LIMIT,
            )
        except OSError as e:
            raise SecretDetectorStartupError(f"cannot run {self._binary}: {e}") from e
        assert process.stdout is not None and process.stderr is not None
        try:
            async with asyncio.timeout(self._ready_timeout):
                line = await process.stdout.readline()
        except TimeoutError:
            await kill_process(process)
            raise SecretDetectorStartupError(
                f"no ready line within {self._ready_timeout}s"
            ) from None
        if not line:
            await process.wait()
            stderr = (await process.stderr.read()).decode(errors="replace").strip()
            raise SecretDetectorStartupError(
                f"exited with {process.returncode} before ready: {stderr}"
            )
        try:
            ready = _Ready.model_validate_json(line)
        except ValidationError as e:
            await kill_process(process)
            raise SecretDetectorStartupError("malformed ready line") from e
        if ready.gitleaks != self._pinned_version:
            await kill_process(process)
            raise SecretDetectorStartupError(
                f"gitleaks version {ready.gitleaks} differs from pinned {self._pinned_version}"
            )
        self.version = ready.gitleaks
        return process

    async def _call(self, texts: Sequence[str]) -> list[list[Finding]]:
        # A process that died between requests harmed no request; replace it before this one.
        if self._process is None or self._process.returncode is not None:
            try:
                self._process = await self._spawn()
            except SecretDetectorStartupError as e:
                raise SecretDetectorUnavailable("secret detector failed to restart") from e
        process = self._process
        assert process.stdin is not None and process.stdout is not None
        self._last_id += 1
        request = json.dumps({"id": self._last_id, "texts": list(texts)}) + "\n"
        try:
            async with asyncio.timeout(self.call_timeout):
                process.stdin.write(request.encode())
                await process.stdin.drain()
                line = await process.stdout.readline()
            if not line:
                raise EOFError("secret detector closed its output")
            response = _Response.model_validate_json(line)
            if response.id != self._last_id or len(response.findings) != len(texts):
                raise ValueError("response does not match request")
        except (TimeoutError, OSError, EOFError, ValueError) as e:
            # ValidationError is a ValueError. The cause stays generic: findings hold secrets.
            self._process = None
            await kill_process(process)
            raise SecretDetectorUnavailable(
                "secret detector crashed, timed out or answered malformed"
            ) from e
        return response.findings


def findings_to_spans(text: str, findings: Sequence[Finding]) -> list[DetectedSpan]:
    line_starts = [0] + [m.end() for m in _NEWLINE.finditer(text)]

    def line_bounds(first: int, last: int) -> tuple[int, int]:
        if not 0 <= first <= last < len(line_starts):
            raise ScrubFailed("secret detector reported a line outside the text")
        end = line_starts[last + 1] if last + 1 < len(line_starts) else len(text)
        return line_starts[first], end

    spans: list[DetectedSpan] = []
    for f in findings:
        if f.decoded:
            start, end = line_bounds(f.start_line, f.end_line)
            spans.extend(_encoded_runs(text, start, end, f))
            continue
        # end_line can understate a multi-line secret (0 for a key ending a text with no final
        # newline); the secret itself says how many lines it covers.
        start, end = line_bounds(f.start_line, max(f.end_line, f.start_line + f.secret.count("\n")))
        found = False
        pos = text.find(f.secret, start, end)
        while pos >= 0:
            spans.append(DetectedSpan(pos, pos + len(f.secret), Category.SECRET, Stage.LOCAL))
            found = True
            pos = text.find(f.secret, pos + 1, end)
        if not found:
            raise ScrubFailed("secret detector finding could not be located")
    return spans


def _encoded_runs(text: str, start: int, end: int, f: Finding) -> list[DetectedSpan]:
    """The encoded segment holding a decoded secret. Columns are approximate: widen to runs."""
    if f.start_line != f.end_line:
        raise ScrubFailed("encoded secret spans several lines")
    lo = start + max(0, f.start_column - 2)
    hi = min(end, start + f.end_column + 1)
    runs = [
        DetectedSpan(m.start(), m.end(), Category.SECRET, Stage.LOCAL)
        for m in _ENCODING_RUN.finditer(text, start, end)
        if m.start() < hi and m.end() > lo
    ]
    if not runs:
        raise ScrubFailed("encoded secret could not be located")
    return runs
