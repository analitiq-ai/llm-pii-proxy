"""Startup wiring (T045): lists loaded before serving, secret detector started and stopped."""

import httpx
import pytest

from claude_pii_proxy.__main__ import build_app
from claude_pii_proxy.detection.gitleaks import SecretDetectorStartupError
from claude_pii_proxy.operator_lists import OperatorListsError
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
