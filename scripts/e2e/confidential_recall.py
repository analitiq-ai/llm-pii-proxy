"""SC-005: share of tests/fixtures/context-pii/ examples the full pipeline replaces.

Manual: calls the real Privatemode proxy at PII_PROXY_CONFIDENTIAL_URL.

    uv run python -m scripts.e2e.confidential_recall [--context-chars N]

With --context-chars, each example is sent as a session sends it: in one chunk after a leaf of N
characters of other text (stdlib source). A rate-limited example is retried after a wait; one that
still gets no answer is reported as failed and left out of the rate.

Exits 0 at or above the 70% target, 1 below it, 2 if any example failed (measurement incomplete).
Prints example numbers only, never text.
"""

import argparse
import asyncio
import json
import re
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from claude_pii_proxy.config import Settings
from claude_pii_proxy.detection.confidential import ConfidentialDetector
from claude_pii_proxy.detection.gitleaks import SecretDetector
from claude_pii_proxy.detection.presidio import PresidioDetector, build_engine
from claude_pii_proxy.errors import ConfidentialUnavailable
from claude_pii_proxy.report import ScrubReport
from claude_pii_proxy.scrub import LeafResult, Pipeline
from claude_pii_proxy.vault import Vault
from tests.fixtures.filler import stdlib_code

EXAMPLES = Path(__file__).parents[2] / "tests" / "fixtures" / "context-pii" / "examples.jsonl"
TARGET = 0.70
# Privatemode limits prompt tokens per minute; a 20k-character chunk is about 5k tokens.
ATTEMPTS = 4
RETRY_WAIT_S = 30
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


async def with_retries(
    scrub: Callable[[], Awaitable[LeafResult]],
    attempts: int,
    wait_s: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> LeafResult:
    """Retry only an unavailable pass (rate limit, timeout); other failures are final."""
    result = await scrub()
    for _ in range(attempts - 1):
        if not isinstance(result, ConfidentialUnavailable):
            break
        await sleep(wait_s)
        result = await scrub()
    return result


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
            missed, failed = [], []
            for i, example in enumerate(examples, 1):
                leaves = session_leaves(example["text"], filler, i, context_chars)

                async def scrub_example(leaves: list[str] = leaves) -> LeafResult:
                    return (await pipeline.scrub(leaves, Vault(), ScrubReport()))[-1]

                scrubbed = await with_retries(scrub_example, ATTEMPTS, RETRY_WAIT_S)
                if isinstance(scrubbed, Exception):
                    print(f"example {i}: failed ({scrubbed})")
                    failed.append(i)
                elif not replaced(example["phrase"], scrubbed):
                    missed.append(i)
    finally:
        await secrets.close()
    answered = len(examples) - len(failed)
    rate = (answered - len(missed)) / answered if answered else 0.0
    print(
        f"replaced {answered - len(missed)}/{answered} answered = {rate:.0%} (target {TARGET:.0%})"
    )
    print(f"missed examples: {missed}")
    if failed:
        print(f"failed examples: {failed} (measurement incomplete)")
        return 2
    return 0 if rate >= TARGET else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-chars", type=int, default=0)
    sys.exit(asyncio.run(main(parser.parse_args().context_chars)))
