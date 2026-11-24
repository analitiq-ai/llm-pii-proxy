"""tests/fixtures/context-pii/ holds examples the local pass misses (spec US2 Independent Test)."""

import json

from llm_pii_proxy.detection.presidio import PresidioDetector
from tests.conftest import FIXTURES

EXAMPLES = [
    json.loads(line)
    for line in (FIXTURES / "context-pii" / "examples.jsonl").read_text().splitlines()
]


def test_at_least_30_examples_each_labelled_with_an_exact_phrase():
    assert len(EXAMPLES) >= 30
    assert all(e["phrase"] and e["phrase"] in e["text"] for e in EXAMPLES)


async def test_local_pass_leaves_every_phrase_partly_unscrubbed(presidio_engine):
    texts = [e["text"] for e in EXAMPLES]
    found = await PresidioDetector(presidio_engine, min_score=0.4).detect(texts)
    fully_covered = []
    for example, spans in zip(EXAMPLES, found, strict=True):
        start = example["text"].index(example["phrase"])
        chars = {
            i
            for i in range(start, start + len(example["phrase"]))
            if not example["text"][i].isspace()
        }
        if chars <= {i for s in spans for i in range(s.start, s.end)}:
            fully_covered.append(example["phrase"])
    assert fully_covered == []
