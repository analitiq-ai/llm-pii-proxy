import threading
from dataclasses import dataclass
from pathlib import Path

import pytest

from claude_pii_proxy.detection import Stage
from claude_pii_proxy.detection.presidio import (
    ENTITY_CATEGORIES,
    NER_CHUNK_CHARS,
    PresidioDetector,
    build_engine,
)
from claude_pii_proxy.vault import Category

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture(scope="module")
def engine():
    return build_engine()


@pytest.fixture(scope="module")
def detector(engine):
    return PresidioDetector(engine, min_score=0.4)


def found(text, spans):
    return {(text[s.start : s.end], s.category) for s in spans}


def test_entity_map_is_research_r5():
    assert ENTITY_CATEGORIES == {
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


def test_spacy_pipeline_is_tok2vec_and_ner_only(engine):
    assert engine.nlp_engine.nlp["en"].pipe_names == ["tok2vec", "ner"]


async def test_categories_from_real_engine(detector):
    text = (
        "Margaret Ellsworth lives in Boston. Mail margaret.ellsworth@brightwaterdental.co.uk "
        "or call +1 212-555-0143. Card 4539 1488 0343 6467, IBAN GB82WEST12345698765432, "
        "host 203.0.113.57."
    )
    (spans,) = await detector.detect([text])
    got = found(text, spans)
    assert ("Margaret Ellsworth", Category.PERSON) in got
    assert ("Boston", Category.ADDRESS) in got
    assert ("margaret.ellsworth@brightwaterdental.co.uk", Category.EMAIL) in got
    assert ("4539 1488 0343 6467", Category.CARD) in got
    assert ("GB82WEST12345698765432", Category.IBAN) in got
    assert ("203.0.113.57", Category.IP) in got
    assert all(s.stage is Stage.LOCAL for s in spans)


async def test_excluded_entities_never_returned(detector):
    text = "On January 5, 2024 the Americans visited https://docs.example.org/page today."
    (spans,) = await detector.detect([text])
    texts = {text[s.start : s.end] for s in spans}
    assert not texts & {"January 5, 2024", "Americans", "https://docs.example.org/page"}


async def test_one_result_list_per_text(detector):
    assert len(await detector.detect(["", "x = 1", "Ann Lee"])) == 3


# A fake engine pins down chunking, offsets, the score threshold and threading.


@dataclass
class Result:
    entity_type: str
    start: int
    end: int
    score: float


class FakeEngine:
    def __init__(self):
        self.calls = []
        self.threads = set()

    def analyze(self, text, language, entities, score_threshold):
        self.calls.append((len(text), language, tuple(entities), score_threshold))
        self.threads.add(threading.get_ident())
        i = text.find("Ann Lee")
        return [Result("PERSON", i, i + 7, 0.85)] if i >= 0 else []


async def test_passes_entities_language_and_threshold():
    engine = FakeEngine()
    await PresidioDetector(engine, min_score=0.6).detect(["hello"])
    assert engine.calls == [(5, "en", tuple(ENTITY_CATEGORIES), 0.6)]


async def test_runs_off_the_event_loop_thread():
    engine = FakeEngine()
    await PresidioDetector(engine, min_score=0.4).detect(["hello"])
    assert threading.get_ident() not in engine.threads


async def test_long_leaf_is_chunked_and_overlap_reported_once():
    engine = FakeEngine()
    # One line over the chunk size: hard split, pieces overlap by 200, the name is in the overlap.
    pos = NER_CHUNK_CHARS - 100
    text = "x" * pos + "Ann Lee" + "y" * 50_000
    (spans,) = await PresidioDetector(engine, min_score=0.4).detect([text])
    assert len(engine.calls) == 2
    assert [(s.start, s.end) for s in spans] == [(pos, pos + 7)]


async def test_names_in_csv_rows_found_whole(detector):
    # spaCy's default tokenizer keeps "1,Margaret" as one token, and NER then misses the name.
    text = (FIXTURES / "pii-project" / "customers.csv").read_text()
    (spans,) = await detector.detect([text])
    got = found(text, spans)
    assert ("Margaret Ellsworth", Category.PERSON) in got
    assert ("Tobias Quennell", Category.PERSON) in got
