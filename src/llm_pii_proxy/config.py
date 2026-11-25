import ipaddress
from pathlib import Path
from typing import Annotated, Any, Self

import httpx
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from llm_pii_proxy.detection.chunking import DEFAULT_OVERLAP
from llm_pii_proxy.tracing.record import Leg

# The secret detector must report exactly this; another gitleaks build may apply different rules.
PINNED_GITLEAKS_VERSION = "v8.30.1"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PII_PROXY_", frozen=True, extra="forbid")

    host: str = "127.0.0.1"
    port: int = Field(8787, ge=1, le=65535)
    viewer_port: int = Field(8765, ge=1, le=65535)
    upstream_url: str = "https://api.anthropic.com"
    min_score: float = Field(0.4, ge=0, le=1)
    session_idle_ttl_s: int = Field(43200, gt=0)
    secret_detector_path: Path = Path(".tools/gitleaks-sidecar")
    lists_path: Path = Path("~/.config/llm-pii-proxy/lists.toml")
    confidential_enabled: bool = False
    confidential_url: str = "http://127.0.0.1:8080"
    confidential_model: str = "gpt-oss-120b"
    confidential_timeout_s: float = Field(30, gt=0)
    confidential_chunk_chars: int = Field(6000, gt=DEFAULT_OVERLAP)
    confidential_concurrency: int = Field(4, ge=1)
    media_enabled: bool = False
    media_ocr_model: str = "deepseek-ocr-2"
    media_vision_model: str = "glm-5.3-flash"
    media_concurrency: int = Field(4, ge=1)
    media_cache_entries: int = Field(256, ge=1)
    trace_legs: Annotated[frozenset[Leg], NoDecode] = frozenset()
    trace_dir: Path = Path("~/.local/state/llm-pii-proxy/traces")
    trace_ttl_h: float = Field(24, gt=0)
    trace_raw: bool = False

    @field_validator("host")
    @classmethod
    def _loopback_only(cls, host: str) -> str:
        # An IP literal only: a name like "localhost" can resolve to a non-loopback address.
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise ValueError("PII_PROXY_HOST must be a loopback IP address")
        return host

    @field_validator("upstream_url", "confidential_url")
    @classmethod
    def _base_url(cls, url: str) -> str:
        parsed = httpx.URL(url)
        if parsed.scheme not in ("http", "https") or not parsed.host:
            raise ValueError("must be an http(s) URL")
        if url.endswith("/"):
            raise ValueError("must not end with '/': request paths are appended to it")
        return url

    @field_validator("trace_legs", mode="before")
    @classmethod
    def _comma_separated(cls, legs: Any) -> Any:
        if isinstance(legs, str):
            return frozenset(name.strip() for name in legs.split(",") if name.strip())
        return legs

    @model_validator(mode="after")
    def _raw_legs_opted_in(self) -> Self:
        raw = sorted(leg for leg in self.trace_legs if leg.raw)
        if raw and not self.trace_raw:
            raise ValueError(
                f"trace legs {','.join(raw)} hold real values: "
                "set PII_PROXY_TRACE_RAW=true to record them"
            )
        return self

    @field_validator("lists_path", "secret_detector_path", "trace_dir")
    @classmethod
    def _absolute(cls, path: Path) -> Path:
        # Against the startup cwd: the secret detector itself runs with cwd "/".
        return path.expanduser().absolute()
