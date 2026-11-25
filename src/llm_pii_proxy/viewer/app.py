"""The trace viewer's server: the page's own files, and the text of the trace directory's `*.jsonl`
files at `/traces`. Answers only requests addressed to 127.0.0.1:<port> or localhost:<port>."""

from importlib.resources import files
from mimetypes import guess_type
from pathlib import Path

from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp

HOST = "127.0.0.1"
NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def _page_files() -> dict[str, tuple[bytes, str]]:
    """Read once at startup, so no request path is ever joined to a directory."""
    static = files("llm_pii_proxy.viewer") / "static"
    pages: dict[str, tuple[bytes, str]] = {}

    def walk(node, prefix: str) -> None:
        for child in node.iterdir():
            if child.is_dir():
                walk(child, f"{prefix}{child.name}/")
            else:
                content_type = guess_type(child.name)[0] or "application/octet-stream"
                if content_type.startswith("text/") or content_type.endswith("javascript"):
                    content_type += "; charset=utf-8"
                pages[f"/{prefix}{child.name}"] = (child.read_bytes(), content_type)

    walk(static, "")
    pages["/"] = pages["/index.html"]
    return pages


def _read(path: Path) -> dict[str, str]:
    try:
        return {"name": path.name, "text": path.read_bytes().decode("utf-8", errors="replace")}
    except OSError as error:
        # The class name only: a message can carry a path.
        return {"name": path.name, "error": type(error).__name__}


def create_viewer_app(trace_dir: Path, port: int) -> ASGIApp:
    pages = _page_files()
    expected_host = f"{HOST}:{port}"
    # Browsers resolve `localhost` to loopback themselves, so no other site can send it as its Host.
    own_hosts = {expected_host, f"localhost:{port}"}

    async def traces(_: Request) -> Response:
        # A missing directory raises here, so the page shows an error, not an empty list.
        names = sorted(
            e.name for e in trace_dir.iterdir() if e.name.endswith(".jsonl") and e.is_file()
        )
        return JSONResponse({"files": [_read(trace_dir / n) for n in names]}, headers=NO_STORE)

    async def page(request: Request) -> Response:
        found = pages.get(request.url.path)
        if found is None:
            return Response(status_code=404)
        body, content_type = found
        return Response(body, media_type=content_type, headers=NO_STORE)

    app = Starlette(routes=[Route("/traces", traces), Route("/{path:path}", page)])

    async def own_address_only(scope, receive, send):
        # A page on another site that points its own name at 127.0.0.1 (DNS rebinding) sends that
        # name here, and is refused.
        if scope["type"] == "http" and Headers(scope=scope).get("host") not in own_hosts:
            refusal = f"This viewer answers only http://{expected_host}/ - open that address.\n"
            await Response(refusal, status_code=421, media_type="text/plain")(scope, receive, send)
            return
        await app(scope, receive, send)

    return own_address_only
