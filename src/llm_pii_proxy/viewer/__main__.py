"""Entry point: `python -m llm_pii_proxy.viewer` serves the trace viewer on the configured trace
directory."""

import logging

import uvicorn

from llm_pii_proxy.config import Settings
from llm_pii_proxy.viewer.app import HOST, create_viewer_app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    if not settings.trace_dir.is_dir():
        raise SystemExit(f"trace directory {settings.trace_dir} does not exist")
    print(
        f"Trace viewer: http://{HOST}:{settings.viewer_port}/  ({settings.trace_dir})", flush=True
    )
    uvicorn.run(
        create_viewer_app(settings.trace_dir, settings.viewer_port),
        host=HOST,
        port=settings.viewer_port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
