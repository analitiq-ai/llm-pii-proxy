import re
from collections.abc import Sequence
from pathlib import Path

import pytest
import respx

from claude_pii_proxy.config import Settings
from claude_pii_proxy.detection import DetectedSpan, Stage
from claude_pii_proxy.operator_lists import OperatorLists
from claude_pii_proxy.vault import Category

UPSTREAM = "https://upstream.test"
REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        upstream_url=UPSTREAM,
        lists_path=tmp_path / "no-lists.toml",
        secret_detector_path=REPO_ROOT / ".tools" / "gitleaks-sidecar",
    )


@pytest.fixture
def operator_lists() -> OperatorLists:
    return OperatorLists()


class RegexDetector:
    """Deterministic test double for the Detector protocol: no spaCy, no Go binary."""

    PATTERNS = (
        (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), Category.EMAIL),
        (re.compile(r"SECRET-[A-Z0-9]+"), Category.SECRET),
    )

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def detect(self, texts: Sequence[str]) -> list[list[DetectedSpan]]:
        self.calls.append(list(texts))
        return [
            [
                DetectedSpan(m.start(), m.end(), category, Stage.LOCAL)
                for pattern, category in self.PATTERNS
                for m in pattern.finditer(text)
            ]
            for text in texts
        ]


@pytest.fixture
def regex_detector() -> RegexDetector:
    return RegexDetector()


@pytest.fixture
def upstream():
    """Mocked Anthropic API. Every request the proxy forwards is recorded in `upstream.calls`."""
    with respx.mock(base_url=UPSTREAM, assert_all_called=False) as router:
        yield router
