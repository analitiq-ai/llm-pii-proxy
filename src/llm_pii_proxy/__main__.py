"""Entry point: `python -m llm_pii_proxy`."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import uvicorn
from presidio_analyzer import AnalyzerEngine
from starlette.applications import Starlette

from llm_pii_proxy import operator_lists
from llm_pii_proxy.app import create_app
from llm_pii_proxy.config import Settings
from llm_pii_proxy.detection.confidential import ConfidentialDetector
from llm_pii_proxy.detection.gitleaks import SecretDetector
from llm_pii_proxy.detection.presidio import PresidioDetector, build_engine
from llm_pii_proxy.scrub import Pipeline
from llm_pii_proxy.sessions import SessionStore

# Connect fails fast; read is long because a streamed answer can pause while the model thinks.
UPSTREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=60.0, pool=10.0)


def build_app(settings: Settings, engine: AnalyzerEngine) -> Starlette:
    """Everything that can fail on configuration fails here, before the port is opened."""
    lists = operator_lists.load(settings.lists_path)
    secrets = SecretDetector(settings.secret_detector_path)
    upstream = httpx.AsyncClient(base_url=settings.upstream_url, timeout=UPSTREAM_TIMEOUT)
    privatemode, confidential = None, None
    if settings.confidential_enabled:
        privatemode = httpx.AsyncClient(
            base_url=settings.confidential_url, timeout=settings.confidential_timeout_s
        )
        confidential = ConfidentialDetector(
            privatemode,
            settings.confidential_model,
            settings.confidential_chunk_chars,
            settings.confidential_concurrency,
        )

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        try:
            await secrets.start()
            yield
        finally:
            await secrets.close()
            await upstream.aclose()
            if privatemode is not None:
                await privatemode.aclose()

    return create_app(
        pipeline=Pipeline(
            [PresidioDetector(engine, settings.min_score), secrets],
            lists.never_scrub,
            confidential=confidential,
        ),
        sessions=SessionStore(lists.always_scrub, settings.session_idle_ttl_s),
        upstream=upstream,
        lifespan=lifespan,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings()
    app = build_app(settings, build_engine())
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)


if __name__ == "__main__":
    main()
