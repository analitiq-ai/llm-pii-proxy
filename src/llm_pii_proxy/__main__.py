"""Entry point: `python -m llm_pii_proxy`."""

import asyncio
import logging
import os
import time
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
from llm_pii_proxy.media import MediaConverter, require_pdf_renderer
from llm_pii_proxy.scrub import Pipeline
from llm_pii_proxy.sessions import SessionStore
from llm_pii_proxy.tracing.file_tap import FileTap, sweep_periodically
from llm_pii_proxy.tracing.record import NullTap, Tap

log = logging.getLogger(__name__)

# Connect fails fast; read is long because a streamed answer can pause while the model thinks.
UPSTREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=60.0, pool=10.0)
TRACE_SWEEP_INTERVAL_S = 3600


def build_app(settings: Settings, engine: AnalyzerEngine) -> Starlette:
    """Everything that can fail on configuration fails here, before the port is opened."""
    lists = operator_lists.load(settings.lists_path)
    secrets = SecretDetector(settings.secret_detector_path)
    upstream = httpx.AsyncClient(base_url=settings.upstream_url, timeout=UPSTREAM_TIMEOUT)
    if settings.media_enabled:
        require_pdf_renderer()
    privatemode, confidential, media = None, None, None
    if settings.confidential_enabled or settings.media_enabled:
        privatemode = httpx.AsyncClient(
            base_url=settings.confidential_url, timeout=settings.confidential_timeout_s
        )
    if settings.confidential_enabled:
        confidential = ConfidentialDetector(
            privatemode,
            settings.confidential_model,
            settings.confidential_chunk_chars,
            settings.confidential_concurrency,
        )
    if settings.media_enabled:
        media = MediaConverter(
            privatemode,
            ocr_model=settings.media_ocr_model,
            vision_model=settings.media_vision_model,
            concurrency=settings.media_concurrency,
            cache_entries=settings.media_cache_entries,
            render_timeout_s=settings.confidential_timeout_s,
        )
    file_tap = _open_trace_output(settings)
    tap: Tap = NullTap() if file_tap is None else file_tap

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        sweeper = None
        if file_tap is not None:
            sweeper = asyncio.create_task(
                sweep_periodically(file_tap, TRACE_SWEEP_INTERVAL_S), name="trace-retention-sweep"
            )
        try:
            await secrets.start()
            yield
        finally:
            if sweeper is not None:
                sweeper.cancel()
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
        tap=tap,
        trace_legs=settings.trace_legs,
        media=media,
    )


def _open_trace_output(settings: Settings) -> FileTap | None:
    if not settings.trace_legs:
        return None
    file_tap = FileTap.open(settings.trace_dir, settings.trace_ttl_h * 3600, os.getuid())
    file_tap.sweep(time.time())
    raw = any(leg.raw for leg in settings.trace_legs)
    log.warning(
        "payload tracing is on: legs %s are written to %s%s",
        ",".join(sorted(settings.trace_legs)),
        settings.trace_dir,
        "; real values are written to disk" if raw else "",
    )
    return file_tap


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings()
    app = build_app(settings, build_engine())
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)


if __name__ == "__main__":
    main()
