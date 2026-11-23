import threading
from dataclasses import dataclass

import pytest

from claude_pii_proxy.detection import Stage, trim_span
from claude_pii_proxy.detection.presidio import (
    ENTITY_CATEGORIES,
    NER_CHUNK_CHARS,
    PresidioDetector,
)
from claude_pii_proxy.vault import Category
from tests.conftest import FIXTURES


@pytest.fixture(scope="module")
def detector(presidio_engine):
    return PresidioDetector(presidio_engine, min_score=0.4)


def found(text, spans):
    return {(text[s.start : s.end], s.category) for s in spans}


def test_entity_map_is_research_r5():
    assert ENTITY_CATEGORIES == {
        "PERSON": Category.PERSON,
        "LOCATION": Category.ADDRESS,
        "STREET_ADDRESS": Category.ADDRESS,
        "EMAIL_ADDRESS": Category.EMAIL,
        "PHONE_NUMBER": Category.PHONE,
        "CREDIT_CARD": Category.CARD,
        "IBAN_CODE": Category.IBAN,
        "US_SSN": Category.NATIONALID,
        "UK_NHS": Category.NATIONALID,
        "IP_ADDRESS": Category.IP,
    }


def test_spacy_pipeline_is_tok2vec_and_ner_only(presidio_engine):
    assert presidio_engine.nlp_engine.nlp["en"].pipe_names == ["tok2vec", "ner"]


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


async def test_mastercard_2_series_found(detector):
    # Presidio's card pattern predates the 2221-2720 Mastercard range (issued since 2017).
    text = "card 2717 9722 0510 5505 and 2221000000000009 and invalid 2717 9722 0510 5506"
    (spans,) = await detector.detect([text])
    got = found(text, spans)
    assert ("2717 9722 0510 5505", Category.CARD) in got
    assert ("2221000000000009", Category.CARD) in got
    assert ("2717 9722 0510 5506", Category.CARD) not in got  # fails the Luhn check


async def test_names_in_key_value_fields_found(detector):
    text = 'WARN billing card declined holder="Gail Harrison"\nINFO owner=Teresa Phillips\n'
    (spans,) = await detector.detect([text])
    got = found(text, [t for s in spans for t in trim_span(text, s)])
    assert ("Gail Harrison", Category.PERSON) in got
    assert ("Teresa Phillips", Category.PERSON) in got


STREET_ADDRESSES = [
    "1600 Pennsylvania Avenue NW, Washington, DC 20500",
    "350 Fifth Ave, New York, NY 10118",
    "4104 James Passage, West Brendafort, NY 37660-1234",
    "742 Evergreen Terrace",
    "221B Baker Street, London NW1 6XE",
    "10 Downing Street, London SW1A 2AA",
    "3 Abbey Rd",
    "Springfield, IL 62704",
    "EC1A 1BB",
]


@pytest.mark.parametrize("address", STREET_ADDRESSES)
async def test_street_address_found_whole(detector, address):
    text = f"Ship the replacement to {address} by Friday.\n"
    (spans,) = await detector.detect([text])
    assert (address, Category.ADDRESS) in found(text, spans)


async def test_code_and_prose_numbers_are_not_addresses(detector):
    text = (
        "HTTP 404 Not Found after 3 Retries; see line 12 Error Handling.\n"
        "for i in range(10): Way = 2\nThe 2 Way handshake took 30 Seconds.\n"
    )
    (spans,) = await detector.detect([text])
    assert not [s for s in spans if s.category is Category.ADDRESS]
