"""Realistic non-PII text for benchmarks and manual runs, fixed by the pinned interpreter."""

import ast
import asyncio
from pathlib import Path

# Long enough to be explanation, not a one-line summary.
_MIN_DOCSTRING_CHARS = 200


def stdlib_code() -> str:
    """Real source code."""
    return "".join(p.read_text() for p in sorted(Path(asyncio.__file__).parent.glob("*.py")))


def stdlib_prose() -> str:
    """Docstrings of the top-level stdlib modules: prose, as Claude Code's system prompt is."""
    docs = []
    for path in sorted(Path(ast.__file__).parent.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                doc = ast.get_docstring(node)
                if doc and len(doc) > _MIN_DOCSTRING_CHARS:
                    docs.append(doc)
    return "\n\n".join(docs)
