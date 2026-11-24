"""Realistic non-PII text for benchmarks and manual measurements."""

import asyncio
from pathlib import Path


def stdlib_code() -> str:
    """Real source code, fixed by the pinned interpreter version."""
    return "".join(p.read_text() for p in sorted(Path(asyncio.__file__).parent.glob("*.py")))
