"""mitmproxy addon: save every upstream request/response pair to $CAPTURE_DIR.

    mitmdump --mode reverse:https://api.anthropic.com -p 8788 -s scripts/e2e/recorder.py

with the proxy's PII_PROXY_UPSTREAM_URL=http://127.0.0.1:8788, so it records what leaves the
machine. The capture holds whatever was sent: keep it outside any repository and delete it after
analysis.
"""

import itertools
import json
import os
from pathlib import Path

OUT = Path(os.environ["CAPTURE_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
_counter = itertools.count(1)


def response(flow) -> None:
    stem = f"{next(_counter):04d}"
    meta = {
        "method": flow.request.method,
        "path": flow.request.path,
        "status": flow.response.status_code,
        "session": flow.request.headers.get("x-claude-code-session-id"),
    }
    OUT.joinpath(f"{stem}.meta.json").write_text(json.dumps(meta))
    # Decoded: a gzip body would hide planted values and thinking blocks from analyze.py.
    OUT.joinpath(f"{stem}.req").write_bytes(flow.request.content or b"")
    OUT.joinpath(f"{stem}.resp").write_bytes(flow.response.content or b"")
