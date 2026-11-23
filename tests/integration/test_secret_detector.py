"""The secret detector process (contracts/secret-detector.md). Needs the binary; never skipped."""

import asyncio
import os
import signal
from pathlib import Path

import pytest

from claude_pii_proxy.config import PINNED_GITLEAKS_VERSION
from claude_pii_proxy.detection import DetectedSpan
from claude_pii_proxy.detection.gitleaks import (
    Finding,
    SecretDetector,
    SecretDetectorStartupError,
    findings_to_spans,
)
from claude_pii_proxy.errors import ScrubFailed, SecretDetectorUnavailable
from claude_pii_proxy.vault import Category

REPO = Path(__file__).resolve().parents[2]
BINARY = REPO / ".tools" / "gitleaks-sidecar"

# Concatenated so the repository itself never holds a provider-format key.
STRIPE = "sk_" + "live_51HqLyjWDarjtT1zdp7dcXNHDiQ0kq5mQSbeSXgqqqqqqqqq"
SLACK = "xox" + "b-2938472938-2938472938472-aBcDeFgHiJkLmNoPqRsTuVwX"
PRIVATE_KEY = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEpAIBAAKCAQEA0Z3VS5JJcds3xfn/ygWyF8PbnGy0AHB7MkGgJ8Hn0eQyT5pL\n"
    "q8kW2Xv6r3Yt9mN1bC4dE7fG0hJ2kL5nP8qS1tU4wX7zA0cD3fG6iK9mN2pQ5sV8\n"
    "-----END RSA PRIVATE KEY-----"
)
GITHUB = "ghp_R8kT2mQ9vX4nB7cL1pW6zH3jF5dS0aYeU2gN"

# (line in a file, secret that must be covered): the 13 samples of research R7.
SAMPLES = [
    ("AWS_ACCESS_KEY_ID=AKIA4HQXZ7MPL2RTB6KD", "AKIA4HQXZ7MPL2RTB6KD"),
    (f"GITHUB_TOKEN={GITHUB}", GITHUB),
    ("DB_PASSWORD=Tr0ub4dor-Quillmere-77", "Tr0ub4dor-Quillmere-77"),
    ('password = "hunter2hunter2"', "hunter2hunter2"),
    ("db_pass: s3cr3t-Pa55w0rd-xyz", "s3cr3t-Pa55w0rd-xyz"),
    ("DATABASE_URL=postgres://admin:Zq8!vR2mLp@db.internal:5432/app", "Zq8!vR2mLp"),
    (f'const stripeKey = "{STRIPE}";', STRIPE),
    (f"SLACK_BOT={SLACK}", SLACK),
    (PRIVATE_KEY, PRIVATE_KEY),
    ('password: "short"', "short"),
    ('config = {"password": "Xy9-kLm2"}', "Xy9-kLm2"),
    ("PGPASSWORD=Zx81Lmn0 ", "Zx81Lmn0"),
    ("  db_pass: plain-text-here", "plain-text-here"),
]
NOT_SECRETS = 'LOG_LEVEL=info\nversion = "3.14.159"\n'
ENCODED_GITHUB = "Z2hwX1I4a1QybVE5dlg0bkI3Y0wxcFc2ekgzakY1ZFMwYVllVTJnTg=="


def covered(text: str, spans: list[DetectedSpan], value: str) -> bool:
    """Every non-newline character of every occurrence of value lies inside a span."""
    start = text.find(value)
    assert start >= 0, "test value missing from text"
    while start >= 0:
        for i in range(start, start + len(value)):
            if text[i] != "\n" and not any(s.start <= i < s.end for s in spans):
                return False
        start = text.find(value, start + 1)
    return True


@pytest.fixture(scope="module")
def binary() -> Path:
    if not BINARY.exists():
        pytest.fail("secret detector not built: run scripts/build-secret-detector.sh")
    return BINARY


@pytest.fixture
async def detector(binary):
    d = SecretDetector(binary)
    await d.start()
    yield d
    await d.close()


async def test_ready_line_reports_pinned_version(detector):
    assert detector.version == PINNED_GITLEAKS_VERSION == "v8.30.1"


async def test_version_mismatch_fails_startup(binary):
    d = SecretDetector(binary, pinned_version="v0.0.1")
    with pytest.raises(SecretDetectorStartupError, match="version"):
        await d.start()


async def test_no_ready_line_fails_startup(tmp_path):
    silent = tmp_path / "silent"
    silent.write_text("#!/bin/sh\nexec sleep 30\n")
    silent.chmod(0o755)
    d = SecretDetector(silent, ready_timeout=0.3)
    with pytest.raises(SecretDetectorStartupError, match="ready"):
        await d.start()


async def test_all_samples_found_one_text_each(detector):
    texts = [line for line, _ in SAMPLES]
    results = await detector.detect(texts)
    assert len(results) == len(texts)
    for (text, secret), spans in zip(SAMPLES, results, strict=True):
        assert covered(text, spans, secret), secret[:12]
        assert all(s.category is Category.SECRET for s in spans)


async def test_samples_found_in_one_file(detector):
    text = "\n".join(line for line, _ in SAMPLES) + "\n" + NOT_SECRETS
    (spans,) = await detector.detect([text])
    for _, secret in SAMPLES:
        assert covered(text, spans, secret), secret[:12]
    flagged = {text[s.start : s.end] for s in spans}
    assert not any("info" in f or "3.14.159" in f for f in flagged)


async def test_nothing_found_in_real_python(detector):
    sources = [p.read_text() for p in sorted((REPO / "src").rglob("*.py"))]
    assert all(spans == [] for spans in await detector.detect(sources))


async def test_gitleaks_allow_comment_does_not_hide_a_secret(detector):
    text = f"GITHUB_TOKEN={GITHUB} # gitleaks:allow"
    (spans,) = await detector.detect([text])
    assert covered(text, spans, GITHUB)


async def test_parent_environment_cannot_change_rules(binary, tmp_path, monkeypatch):
    empty = tmp_path / "empty.toml"
    empty.write_text("[extend]\nuseDefault = false\n")
    monkeypatch.setenv("GITLEAKS_CONFIG", str(empty))
    monkeypatch.setenv("GITLEAKS_CONFIG_TOML", empty.read_text())
    d = SecretDetector(binary)
    await d.start()
    try:
        (spans,) = await d.detect([f"GITHUB_TOKEN={GITHUB}"])
        assert spans
    finally:
        await d.close()


async def test_killed_process_fails_in_flight_call_then_recovers(detector):
    big = "x = 1\n" * 300_000
    call = asyncio.create_task(detector.detect([big]))
    await asyncio.sleep(0.1)
    os.kill(detector.pid, signal.SIGKILL)
    with pytest.raises(SecretDetectorUnavailable):
        await call
    (spans,) = await detector.detect([f"GITHUB_TOKEN={GITHUB}"])
    assert spans


async def test_call_timeout_fails_then_recovers(binary):
    d = SecretDetector(binary, call_timeout=0.05)
    await d.start()
    try:
        with pytest.raises(SecretDetectorUnavailable):
            await d.detect(["x = 1\n" * 300_000])
        d.call_timeout = 10
        (spans,) = await d.detect([f"GITHUB_TOKEN={GITHUB}"])
        assert spans
    finally:
        await d.close()


async def test_base64_encoded_secret_replaced_as_encoded_run(detector):
    text = f"payload:\n  {ENCODED_GITHUB}\nend\n"
    (spans,) = await detector.detect([text])
    assert covered(text, spans, ENCODED_GITHUB)


async def test_empty_input(detector):
    assert await detector.detect([]) == []


# findings_to_spans is pure; these cover answers the real binary doesn't produce on demand.


def finding(**kw) -> Finding:
    base = {"rule": "r", "secret": "s3cret", "start_line": 0, "end_line": 0}
    base.update(start_column=1, end_column=1, tags=[])
    return Finding(**(base | kw))


def test_every_occurrence_within_the_finding_lines():
    text = "a s3cret\nb s3cret s3cret\nc s3cret"
    spans = findings_to_spans(text, [finding(start_line=1, end_line=1)])
    assert [(s.start, s.end) for s in spans] == [(11, 17), (18, 24)]


def test_secret_not_in_its_lines_fails_closed():
    with pytest.raises(ScrubFailed):
        findings_to_spans("a s3cret\nb", [finding(start_line=1, end_line=1)])


def test_line_out_of_range_fails_closed():
    with pytest.raises(ScrubFailed):
        findings_to_spans("a s3cret", [finding(start_line=3, end_line=3)])


def test_decoded_finding_widens_to_encoding_run():
    text = f'k: "{ENCODED_GITHUB}" # x'
    col = text.index(ENCODED_GITHUB) + 2  # deliberately off: columns are approximate
    f = finding(secret=GITHUB, start_column=col, end_column=col + 10, tags=["decoded:base64"])
    (span,) = findings_to_spans(text, [f])
    assert text[span.start : span.end] == ENCODED_GITHUB


def test_decoded_finding_without_encoding_run_fails_closed():
    text = "a      .      b"
    f = finding(secret=GITHUB, start_column=5, end_column=9, tags=["decoded:hex"])
    with pytest.raises(ScrubFailed):
        findings_to_spans(text, [f])
