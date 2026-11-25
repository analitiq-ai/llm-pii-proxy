"""Startup wiring (T045): lists loaded before serving, secret detector started and stopped."""

import asyncio
import json
import logging
import os
import time

import httpx
import pytest
import respx

import llm_pii_proxy.__main__ as main_module
from llm_pii_proxy.__main__ import build_app
from llm_pii_proxy.detection.gitleaks import SecretDetectorStartupError
from llm_pii_proxy.media import PdfRendererMissing
from llm_pii_proxy.operator_lists import OperatorListsError
from llm_pii_proxy.tracing.file_tap import TraceDirError
from llm_pii_proxy.tracing.record import Leg
from tests.conftest import UPSTREAM
from tests.integration.conftest import SESSION, message_response, sent


@pytest.fixture
def lists(tmp_path):
    path = tmp_path / "lists.toml"
    path.write_text('never_scrub = ["Django"]\n\n[always_scrub]\nPERSON = ["qvellmoran"]\n')
    return path


async def test_app_serves_with_lists_and_real_detectors(
    settings, lists, presidio_engine, secret_detector_binary, upstream
):
    app = build_app(settings.model_copy(update={"lists_path": lists}), presidio_engine)
    upstream.post("/v1/messages").mock(return_value=message_response("ok"))
    body = {
        "model": "m",
        "messages": [{"role": "user", "content": "qvellmoran on Django, AKIAZ3MHN4QWRT6YB5UX"}],
    }
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy") as proxy:
            r = await proxy.post("/v1/messages", json=body, headers=SESSION)
    assert r.status_code == 200
    assert sent(upstream)["messages"][0]["content"] == "PII_PERSON_0001 on Django, PII_SECRET_0001"
    assert str(upstream.calls[0].request.url).startswith(UPSTREAM)


def test_invalid_lists_fail_before_serving(settings, tmp_path, presidio_engine):
    bad = tmp_path / "lists.toml"
    bad.write_text("[always_scrub]\nLITERAL = ['x']\n")
    with pytest.raises(OperatorListsError):
        build_app(settings.model_copy(update={"lists_path": bad}), presidio_engine)


async def test_missing_secret_detector_fails_startup(settings, tmp_path, presidio_engine):
    app = build_app(
        settings.model_copy(update={"secret_detector_path": tmp_path / "nope"}), presidio_engine
    )
    with pytest.raises(SecretDetectorStartupError):
        async with app.router.lifespan_context(app):
            pass


PRIVATEMODE = "http://privatemode.test"


def privatemode_answer(*findings: str) -> httpx.Response:
    content = json.dumps({"findings": [{"text": f} for f in findings]})
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


async def post_through(app, text):
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy") as proxy:
            body = {"model": "m", "messages": [{"role": "user", "content": text}]}
            return await proxy.post("/v1/messages", json=body, headers=SESSION)


@pytest.mark.parametrize("enabled", [True, False])
async def test_confidential_pass_runs_only_when_enabled(
    settings, presidio_engine, secret_detector_binary, upstream, enabled
):
    update = {"confidential_enabled": enabled, "confidential_url": PRIVATEMODE}
    app = build_app(settings.model_copy(update=update), presidio_engine)
    upstream.post("/v1/messages").mock(return_value=message_response("ok"))
    with respx.mock(base_url=PRIVATEMODE, assert_all_called=False) as privatemode:
        route = privatemode.post("/v1/chat/completions").mock(
            return_value=privatemode_answer("the mayor's chauffeur")
        )
        r = await post_through(app, "Ask the mayor's chauffeur.")
    assert r.status_code == 200
    assert route.called is enabled
    expected = "Ask PII_CONTEXT_0001." if enabled else "Ask the mayor's chauffeur."
    assert sent(upstream)["messages"][0]["content"] == expected


async def test_claude_code_identity_line_reaches_anthropic_unchanged(
    settings, presidio_engine, secret_detector_binary, upstream
):
    # Anthropic rejects subscription traffic whose identity line was altered (research R3).
    identity = "You are a Claude agent, built on Anthropic's Claude Agent SDK."
    app = build_app(settings, presidio_engine)
    upstream.post("/v1/messages").mock(return_value=message_response("ok"))
    body = {
        "model": "m",
        "system": [{"type": "text", "text": identity}],
        "messages": [{"role": "user", "content": "Hi, I'm Margaret Ellsworth."}],
    }
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy") as proxy:
            r = await proxy.post("/v1/messages", json=body, headers=SESSION)
    assert r.status_code == 200
    assert sent(upstream)["system"][0]["text"] == identity
    assert sent(upstream)["messages"][0]["content"] == "Hi, I'm PII_PERSON_0001."


TRACE_LEGS = frozenset({Leg.UPSTREAM_OUT, Leg.UPSTREAM_IN})


def tracing(settings, trace_dir, legs=TRACE_LEGS):
    return settings.model_copy(update={"trace_legs": legs, "trace_dir": trace_dir})


def test_tracing_off_creates_no_trace_directory(settings, tmp_path, presidio_engine):
    trace_dir = tmp_path / "traces"
    build_app(tracing(settings, trace_dir, frozenset()), presidio_engine)
    assert not trace_dir.exists()


def test_trace_directory_in_git_tree_fails_before_serving(settings, tmp_path, presidio_engine):
    (tmp_path / ".git").mkdir()
    with pytest.raises(TraceDirError, match="git working tree"):
        build_app(tracing(settings, tmp_path / "traces"), presidio_engine)


def test_trace_directory_with_wrong_mode_fails_before_serving(settings, tmp_path, presidio_engine):
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    trace_dir.chmod(0o755)
    with pytest.raises(TraceDirError, match="0755"):
        build_app(tracing(settings, trace_dir), presidio_engine)


def test_tracing_on_warns_once_naming_legs_and_directory(
    settings, tmp_path, presidio_engine, caplog
):
    trace_dir = tmp_path / "traces"
    with caplog.at_level(logging.WARNING):
        build_app(tracing(settings, trace_dir), presidio_engine)
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "upstream_in" in warnings[0] and "upstream_out" in warnings[0]
    assert str(trace_dir) in warnings[0]
    assert "real values" not in warnings[0]


def test_raw_legs_warn_that_real_values_are_written(settings, tmp_path, presidio_engine, caplog):
    with caplog.at_level(logging.WARNING):
        build_app(tracing(settings, tmp_path / "traces", frozenset(Leg)), presidio_engine)
    (warning,) = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert all(leg in warning for leg in Leg)
    assert "real values" in warning


def test_startup_sweeps_expired_traces(settings, tmp_path, presidio_engine):
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir(mode=0o700)
    trace_dir.chmod(0o700)
    expired = trace_dir / "old.jsonl"
    expired.write_text("")
    expired.chmod(0o600)
    old = time.time() - 25 * 3600
    os.utime(expired, (old, old))
    build_app(tracing(settings, trace_dir), presidio_engine)
    assert not expired.exists()


async def test_periodic_sweep_deletes_expired_traces_while_serving(
    settings, tmp_path, presidio_engine, secret_detector_binary, monkeypatch
):
    monkeypatch.setattr(main_module, "TRACE_SWEEP_INTERVAL_S", 0)
    trace_dir = tmp_path / "traces"
    app = build_app(tracing(settings, trace_dir), presidio_engine)
    expired = trace_dir / "old.jsonl"
    expired.write_text("")
    expired.chmod(0o600)
    old = time.time() - 25 * 3600
    os.utime(expired, (old, old))

    def sweep_tasks():
        return [t for t in asyncio.all_tasks() if t.get_name() == "trace-retention-sweep"]

    async with app.router.lifespan_context(app):
        (task,) = sweep_tasks()
        async with asyncio.timeout(5):
            while expired.exists():
                await asyncio.sleep(0.01)
    await asyncio.sleep(0)
    assert task.cancelled()


async def test_media_conversion_runs_when_enabled(
    settings, presidio_engine, secret_detector_binary, upstream
):
    update = {"media_enabled": True, "confidential_url": PRIVATEMODE}
    app = build_app(settings.model_copy(update=update), presidio_engine)
    upstream.post("/v1/messages").mock(return_value=message_response("ok"))
    image = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "iVBO"},
    }
    vision = json.dumps({"document": False, "description": "A cat."})
    with respx.mock(base_url=PRIVATEMODE) as privatemode:
        privatemode.post("/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": vision}}]})
        )
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://proxy") as proxy:
                body = {"model": "m", "messages": [{"role": "user", "content": [image]}]}
                r = await proxy.post("/v1/messages", json=body, headers=SESSION)
    assert r.status_code == 200
    assert sent(upstream)["messages"][0]["content"][0]["text"].endswith("A cat.")


def test_media_enabled_without_pdftoppm_fails_before_serving(
    settings, presidio_engine, tmp_path, monkeypatch
):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(PdfRendererMissing, match="pdftoppm"):
        build_app(settings.model_copy(update={"media_enabled": True}), presidio_engine)
