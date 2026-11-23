"""Session and leaf cache (data-model.md)."""

import asyncio

import pytest

from claude_pii_proxy.detection import DetectedSpan, Stage
from claude_pii_proxy.errors import ScrubFailed
from claude_pii_proxy.report import ScrubReport
from claude_pii_proxy.scrub import Pipeline
from claude_pii_proxy.sessions import SessionStore
from claude_pii_proxy.vault import Category


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class NamesDetector:
    """Flags 'Ann'; each call can be held open and counts the texts it saw."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self.gate: asyncio.Event | None = None
        self.fail_on: str | None = None

    async def detect(self, texts):
        self.seen.extend(texts)
        if self.gate is not None:
            await self.gate.wait()
        if self.fail_on is not None and self.fail_on in texts:
            raise RuntimeError("boom")
        return [
            [DetectedSpan(i, i + 3, Category.PERSON, Stage.LOCAL)]
            if (i := t.find("Ann")) >= 0
            else []
            for t in texts
        ]


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(clock):
    return SessionStore({Category.PERSON: ("jdoe",)}, idle_ttl_s=100, clock=clock)


@pytest.fixture
def detector():
    return NamesDetector()


async def scrub(session, detector, texts):
    return await session.scrub_leaves(texts, Pipeline([detector]), ScrubReport())


def test_new_session_has_seeded_vault(store):
    session = store.get("s1", has_assistant_turns=False)
    assert session.vault.token_for("jdoe") == "PII_PERSON_0001"
    assert store.get("s1", has_assistant_turns=False) is session
    assert store.get("s2", has_assistant_turns=False) is not session


def test_joined_mid_conversation_recorded_from_first_request(store):
    assert store.get("a", has_assistant_turns=True).joined_mid_conversation
    assert not store.get("b", has_assistant_turns=False).joined_mid_conversation
    assert not store.get("b", has_assistant_turns=True).joined_mid_conversation


async def test_cached_leaf_keeps_its_bytes_after_vault_grows(store, detector):
    session = store.get("s", has_assistant_turns=False)
    assert await scrub(session, detector, ["hi Annabel"]) == ["hi PII_PERSON_0002abel"]
    session.vault.assign("hi", Category.PERSON)
    detector.seen.clear()
    assert await scrub(session, detector, ["hi Annabel", "new"]) == [
        "hi PII_PERSON_0002abel",
        "new",
    ]
    assert detector.seen == ["new"]


async def test_concurrent_requests_single_flight(store, detector):
    session = store.get("s", has_assistant_turns=False)
    detector.gate = asyncio.Event()
    first = asyncio.create_task(scrub(session, detector, ["Ann", "mine"]))
    await asyncio.sleep(0)
    second = asyncio.create_task(scrub(session, detector, ["Ann", "theirs"]))
    await asyncio.sleep(0)
    detector.gate.set()
    a, b = await asyncio.gather(first, second)
    assert a[0] == b[0] == "PII_PERSON_0002"
    assert sorted(detector.seen) == ["Ann", "mine", "theirs"]


async def test_duplicate_leaf_in_one_request_scrubbed_once(store, detector):
    session = store.get("s", has_assistant_turns=False)
    assert await scrub(session, detector, ["Ann", "Ann"]) == ["PII_PERSON_0002"] * 2
    assert detector.seen == ["Ann"]


async def test_failure_reaches_every_waiter_and_is_not_cached(store, detector):
    session = store.get("s", has_assistant_turns=False)
    detector.gate = asyncio.Event()
    detector.fail_on = "Ann"
    first = asyncio.create_task(scrub(session, detector, ["Ann"]))
    await asyncio.sleep(0)
    second = asyncio.create_task(scrub(session, detector, ["Ann"]))
    await asyncio.sleep(0)
    detector.gate.set()
    (a,), (b,) = await asyncio.gather(first, second)
    assert isinstance(a, RuntimeError) and b is a
    detector.gate, detector.fail_on = None, None
    assert await scrub(session, detector, ["Ann"]) == ["PII_PERSON_0002"]


async def test_leaf_cached_even_if_another_leaf_of_the_request_fails(store, detector):
    session = store.get("s", has_assistant_turns=False)
    for i in range(9998):
        session.vault.assign(f"x{i}", Category.PERSON)
    ok, failed = await scrub(session, detector, ["fine", "Ann"])
    assert ok == "fine" and isinstance(failed, ScrubFailed)
    detector.seen.clear()
    assert await scrub(session, detector, ["fine"]) == ["fine"]
    assert detector.seen == []


async def test_cancelled_owner_fails_waiters_closed(store, detector):
    session = store.get("s", has_assistant_turns=False)
    detector.gate = asyncio.Event()
    owner = asyncio.create_task(scrub(session, detector, ["Ann"]))
    await asyncio.sleep(0)
    waiter = asyncio.create_task(scrub(session, detector, ["Ann"]))
    await asyncio.sleep(0)
    owner.cancel()
    (result,) = await waiter
    assert isinstance(result, ScrubFailed)
    detector.gate = None
    assert await scrub(session, detector, ["Ann"]) == ["PII_PERSON_0002"]


def test_idle_session_evicted_whole(store, clock):
    old = store.get("old", has_assistant_turns=False)
    clock.now += 50
    active = store.get("active", has_assistant_turns=False)
    clock.now += 60
    assert store.get("active", has_assistant_turns=False) is active
    assert store.get("old", has_assistant_turns=False) is not old


def test_session_used_within_ttl_is_kept(store, clock):
    s = store.get("s", has_assistant_turns=False)
    for _ in range(5):
        clock.now += 90
        assert store.get("s", has_assistant_turns=False) is s
