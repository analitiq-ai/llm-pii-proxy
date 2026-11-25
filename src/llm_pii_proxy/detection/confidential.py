"""Confidential pass via the local Privatemode proxy (contracts/confidential-detection.md).

Input is text the local stage already scrubbed. The Privatemode proxy holds its own API key.
"""

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from llm_pii_proxy.detection import DetectedSpan, Stage
from llm_pii_proxy.detection.chunking import Chunk, chunk
from llm_pii_proxy.errors import PrivatemodeInvalid, ProxyError
from llm_pii_proxy.privatemode import complete
from llm_pii_proxy.vault import Category

SYSTEM_PROMPT = (
    "You find personal data in the user's text: names, nicknames, online handles, and "
    "descriptions that single out one real person, such as a unique role, a rare condition "
    "with a place, a distinctive trait, or a relationship to another person. Report each such"
    " description whole, copied verbatim from the text, including the clauses that make it "
    "identifying, but not the rest of the sentence. Tokens of the form PII_<CATEGORY>_<NNNN> "
    "are already redacted; text around them may still identify someone. Ignore code "
    "identifiers and library names. Respond with JSON only, matching the schema; an empty "
    "findings list if there is none."
)
_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["findings"],
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text"],
                "properties": {"text": {"type": "string", "minLength": 1}},
            },
        }
    },
}
# Leaves are packed one after another, each starting on its own line.
_LEAF_SEPARATOR = "\n"


class _Finding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1)


class _Answer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    findings: list[_Finding]


@dataclass(frozen=True)
class Detection:
    # Per input text: its CONTEXT spans, or why a chunk covering it failed.
    leaves: list[list[DetectedSpan] | ProxyError]
    # Findings not found verbatim in their chunk: the model paraphrased.
    unlocated: int


class ConfidentialDetector:
    def __init__(
        self, client: httpx.AsyncClient, model: str, chunk_chars: int, concurrency: int
    ) -> None:
        self._client = client
        self._model = model
        self._chunk_chars = chunk_chars
        self._slots = asyncio.Semaphore(concurrency)

    async def detect(self, texts: Sequence[str]) -> Detection:
        starts, pos = [], 0
        for text in texts:
            starts.append(pos)
            pos += len(text) + len(_LEAF_SEPARATOR)
        document = _LEAF_SEPARATOR.join(texts)
        chunks = chunk(document, self._chunk_chars)
        outcomes = await asyncio.gather(*(self._ask(c.text) for c in chunks))

        spans: list[set[DetectedSpan]] = [set() for _ in texts]
        failures: dict[int, ProxyError] = {}
        unlocated = 0
        for piece, outcome in zip(chunks, outcomes, strict=True):
            if isinstance(outcome, ProxyError):
                for i in _covered(piece, starts, texts):
                    failures.setdefault(i, outcome)
                continue
            for finding in outcome:
                occurrences = _occurrences(piece, finding)
                unlocated += not occurrences
                for start, end in occurrences:
                    leaf = _leaf_holding(start, end, starts, texts)
                    if leaf is not None:
                        offset = starts[leaf]
                        spans[leaf].add(
                            DetectedSpan(
                                start - offset, end - offset, Category.CONTEXT, Stage.CONFIDENTIAL
                            )
                        )
        leaves: list[list[DetectedSpan] | ProxyError] = [
            failures.get(i) or sorted(s, key=lambda x: (x.start, x.end))
            for i, s in enumerate(spans)
        ]
        return Detection(leaves, unlocated)

    async def _ask(self, text: str) -> list[str] | ProxyError:
        async with self._slots:
            content = await complete(self._client, self._request(text), "confidential pass")
        if isinstance(content, ProxyError):
            return content
        try:
            answer = _Answer.model_validate(json.loads(content))
        except (ValueError, ValidationError):
            return PrivatemodeInvalid("confidential pass returned an invalid answer")
        return [f.text for f in answer.findings]

    def _request(self, text: str) -> dict[str, Any]:
        return {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "pii_findings", "strict": True, "schema": _SCHEMA},
            },
            "temperature": 0,
            "frequency_penalty": 0.1,
            "reasoning_effort": "low",
            "max_completion_tokens": 4096,
        }


def _occurrences(piece: Chunk, finding: str) -> list[tuple[int, int]]:
    """Document offsets of every exact occurrence of a finding in a chunk."""
    found, i = [], piece.text.find(finding)
    while i >= 0:
        found.append((piece.start + i, piece.start + i + len(finding)))
        i = piece.text.find(finding, i + 1)
    return found


def _leaf_holding(start: int, end: int, starts: list[int], texts: Sequence[str]) -> int | None:
    """The leaf containing [start, end) whole, or None if it crosses a leaf boundary."""
    for i, (leaf_start, text) in enumerate(zip(starts, texts, strict=True)):
        if leaf_start <= start and end <= leaf_start + len(text):
            return i
    return None


def _covered(piece: Chunk, starts: list[int], texts: Sequence[str]) -> list[int]:
    end = piece.start + len(piece.text)
    return [
        i
        for i, (leaf_start, text) in enumerate(zip(starts, texts, strict=True))
        if leaf_start < end and piece.start <= leaf_start + len(text)
    ]
