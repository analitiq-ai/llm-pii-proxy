"""SC-005: share of tests/fixtures/context-pii/ examples the full pipeline replaces.

Manual: calls the real Privatemode proxy at PII_PROXY_CONFIDENTIAL_URL.

    uv run python -m scripts.e2e.confidential_recall [--context-chars N]

With --context-chars, each example is sent as a session sends it: in one chunk after a leaf of N
characters of other text (stdlib source). Exits 1 below the 70% target. Prints example numbers only,
never text.
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

import httpx

from claude_pii_proxy.config import Settings
from claude_pii_proxy.detection.confidential import ConfidentialDetector
from claude_pii_proxy.detection.gitleaks import SecretDetector
from claude_pii_proxy.detection.presidio import PresidioDetector, build_engine
from claude_pii_proxy.report import ScrubReport
from claude_pii_proxy.scrub import Pipeline
from claude_pii_proxy.vault import Vault
from tests.fixtures.filler import stdlib_code

EXAMPLES = Path(__file__).parents[2] / "tests" / "fixtures" / "context-pii" / "examples.jsonl"
TARGET = 0.70
# Words that identify no one. A phrase whose other words are all gone is replaced even if the model
# left "the" or "who also" outside its finding.
_STOPWORDS = frozenset(
    {"a", "an", "and", "at", "by", "for", "from", "in", "of", "on", "our", "the", "their", "to"}
    | {"who", "whose", "with", "one", "also"}
)
_WORD = re.compile(r"[\w'’-]+")


def replaced(phrase: str, scrubbed: str) -> bool:
    """True when no content word of the phrase survives as a whole word in the scrubbed text."""
    words = {w for w in _WORD.findall(phrase) if w.lower() not in _STOPWORDS}
    return not any(re.search(rf"(?<![\w'’-]){re.escape(w)}(?![\w'’-])", scrubbed) for w in words)


def session_leaves(text: str, filler: str, index: int, context_chars: int) -> list[str]:
    """The example alone, or after a context leaf: a different slice of filler per example."""
    if not context_chars:
        return [text]
    start = (index * context_chars) % (len(filler) - context_chars)
    return [filler[start : start + context_chars], text]


async def main(context_chars: int) -> int:
    settings = Settings()
    filler = stdlib_code() if context_chars else ""
    examples = [json.loads(line) for line in EXAMPLES.read_text().splitlines() if line]
    secrets = SecretDetector(settings.secret_detector_path)
    await secrets.start()
    try:
        async with httpx.AsyncClient(
            base_url=settings.confidential_url, timeout=settings.confidential_timeout_s
        ) as client:
            pipeline = Pipeline(
                [PresidioDetector(build_engine(), settings.min_score), secrets],
                confidential=ConfidentialDetector(
                    client,
                    settings.confidential_model,
                    settings.confidential_chunk_chars,
                    settings.confidential_concurrency,
                ),
            )
            missed = []
            for i, example in enumerate(examples, 1):
                leaves = session_leaves(example["text"], filler, i, context_chars)
                *_, scrubbed = await pipeline.scrub(leaves, Vault(), ScrubReport())
                if isinstance(scrubbed, Exception):
                    print(f"example {i}: failed closed ({type(scrubbed).__name__})")
                    missed.append(i)
                elif not replaced(example["phrase"], scrubbed):
                    missed.append(i)
    finally:
        await secrets.close()
    rate = 1 - len(missed) / len(examples)
    print(
        f"replaced {len(examples) - len(missed)}/{len(examples)} = {rate:.0%} (target {TARGET:.0%})"
    )
    print(f"missed examples: {missed}")
    return 0 if rate >= TARGET else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-chars", type=int, default=0)
    sys.exit(asyncio.run(main(parser.parse_args().context_chars)))
