"""Local PII detection with Presidio and spaCy (research R5)."""

import asyncio
import threading
from collections.abc import Sequence
from typing import Any, Protocol

from presidio_analyzer import AnalyzerEngine
from presidio_analyzer.nlp_engine import NlpEngineProvider
from spacy.util import compile_infix_regex

from claude_pii_proxy.detection import DetectedSpan, Stage
from claude_pii_proxy.detection.chunking import chunk
from claude_pii_proxy.vault import Category

ENTITY_CATEGORIES: dict[str, Category] = {
    "PERSON": Category.PERSON,
    "LOCATION": Category.ADDRESS,
    "EMAIL_ADDRESS": Category.EMAIL,
    "PHONE_NUMBER": Category.PHONE,
    "CREDIT_CARD": Category.CARD,
    "IBAN_CODE": Category.IBAN,
    "US_SSN": Category.NATIONALID,
    "UK_NHS": Category.NATIONALID,
    "IP_ADDRESS": Category.IP,
}

# spaCy rejects texts over nlp.max_length (1 000 000 chars).
NER_CHUNK_CHARS = 100_000

# S2: Presidio gives identical results without these, at about half the NER time.
_UNUSED_SPACY_COMPONENTS = ("tagger", "parser", "attribute_ruler", "lemmatizer")


class Analyzer(Protocol):
    def analyze(
        self, text: str, language: str, entities: list[str], score_threshold: float
    ) -> list[Any]: ...


def build_engine() -> AnalyzerEngine:
    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_lg"}],
        }
    )
    engine = AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=["en"])
    nlp = engine.nlp_engine.nlp["en"]
    for name in _UNUSED_SPACY_COMPONENTS:
        nlp.disable_pipe(name)
    # The default tokenizer splits a comma only between letters, so "1,Margaret" in a CSV row
    # stays one token and NER misses the name. Data delimiters always separate tokens.
    infixes = [*nlp.Defaults.infixes, r"[,;|]"]
    nlp.tokenizer.infix_finditer = compile_infix_regex(infixes).finditer
    return engine


class PresidioDetector:
    def __init__(self, engine: Analyzer, min_score: float) -> None:
        self._engine = engine
        self._min_score = min_score
        # spaCy doesn't promise thread-safe inference, and the work is CPU-bound, so one at a time.
        self._lock = threading.Lock()

    async def detect(self, texts: Sequence[str]) -> list[list[DetectedSpan]]:
        return await asyncio.to_thread(self._detect_all, list(texts))

    def _detect_all(self, texts: list[str]) -> list[list[DetectedSpan]]:
        with self._lock:
            return [self._detect_one(t) for t in texts]

    def _detect_one(self, text: str) -> list[DetectedSpan]:
        spans: set[DetectedSpan] = set()
        for piece in chunk(text, NER_CHUNK_CHARS):
            for r in self._engine.analyze(
                text=piece.text,
                language="en",
                entities=list(ENTITY_CATEGORIES),
                score_threshold=self._min_score,
            ):
                spans.add(
                    DetectedSpan(
                        piece.start + r.start,
                        piece.start + r.end,
                        ENTITY_CATEGORIES[r.entity_type],
                        Stage.LOCAL,
                    )
                )
        return sorted(spans, key=lambda s: (s.start, s.end))
