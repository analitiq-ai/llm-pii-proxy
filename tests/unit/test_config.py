from pathlib import Path

import pytest
from pydantic import ValidationError

from claude_pii_proxy.config import PINNED_GITLEAKS_VERSION, Settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    import os

    for name in list(os.environ):
        if name.startswith("PII_PROXY_"):
            monkeypatch.delenv(name)


def test_defaults_match_quickstart_table():
    s = Settings()
    assert s.host == "127.0.0.1"
    assert s.port == 8787
    assert s.upstream_url == "https://api.anthropic.com"
    assert s.min_score == 0.4
    assert s.session_idle_ttl_s == 43200
    assert s.secret_detector_path == Path.cwd() / ".tools/gitleaks-sidecar"
    assert s.lists_path == Path("~/.config/claude-pii-proxy/lists.toml").expanduser()
    assert s.confidential_enabled is False
    assert s.confidential_url == "http://127.0.0.1:8080"
    assert s.confidential_model == "gpt-oss-120b"
    assert s.confidential_timeout_s == 30
    assert s.confidential_chunk_chars == 24000
    assert s.confidential_concurrency == 4


def test_reads_prefixed_environment(monkeypatch):
    monkeypatch.setenv("PII_PROXY_PORT", "9000")
    monkeypatch.setenv("PII_PROXY_CONFIDENTIAL_ENABLED", "true")
    s = Settings()
    assert s.port == 9000
    assert s.confidential_enabled is True


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "127.0.0.2"])
def test_loopback_hosts_accepted(host):
    assert Settings(host=host).host == host


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::", "localhost", "example.com"])
def test_non_loopback_hosts_rejected(host):
    with pytest.raises(ValidationError, match="loopback"):
        Settings(host=host)


@pytest.mark.parametrize(
    "field,value",
    [
        ("session_idle_ttl_s", -1),
        ("session_idle_ttl_s", 0),
        ("port", 0),
        ("port", 70000),
        ("min_score", 1.1),
        ("min_score", -0.1),
        ("confidential_timeout_s", 0),
        ("confidential_concurrency", 0),
        ("confidential_chunk_chars", 200),
        ("upstream_url", "ftp://api.anthropic.com"),
        ("upstream_url", "https://api.anthropic.com/"),
    ],
)
def test_invalid_values_raise_at_construction(field, value):
    with pytest.raises(ValidationError):
        Settings(**{field: value})


def test_pinned_gitleaks_version():
    assert PINNED_GITLEAKS_VERSION == "v8.30.1"


def test_secret_detector_path_resolved_against_startup_cwd(monkeypatch, tmp_path):
    # The detector runs with cwd "/", so a relative path must be made absolute up front.
    monkeypatch.chdir(tmp_path)
    assert (
        Settings(secret_detector_path=Path("rel/bin")).secret_detector_path == tmp_path / "rel/bin"
    )
    assert Settings().secret_detector_path == tmp_path / ".tools/gitleaks-sidecar"
