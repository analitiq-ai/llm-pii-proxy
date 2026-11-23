"""Sessions and the per-session leaf cache (data-model.md, Session and Leaf cache)."""

import asyncio
import hashlib
import time
from collections.abc import Callable, Mapping, Sequence

from claude_pii_proxy.errors import ScrubFailed
from claude_pii_proxy.report import ScrubReport, SessionTotals
from claude_pii_proxy.scrub import LeafResult, Pipeline
from claude_pii_proxy.vault import Category, Vault


class Session:
    def __init__(self, vault: Vault, joined_mid_conversation: bool, now: float) -> None:
        self.vault = vault
        # Set when the first request already held assistant turns, e.g. after a proxy restart: the
        # history was scrubbed by an earlier vault, so a thinking-signature rejection is expected.
        self.joined_mid_conversation = joined_mid_conversation
        self.last_seen = now
        self.totals = SessionTotals()
        # Re-sent history must scrub to the bytes sent the first time (thinking signatures), so a
        # leaf's bytes are fixed once computed. A pending future makes concurrent requests share one
        # scrub instead of racing a non-deterministic detector.
        self._leaf_cache: dict[bytes, str] = {}
        self._pending: dict[bytes, asyncio.Future[str]] = {}

    async def scrub_leaves(
        self, texts: Sequence[str], pipeline: Pipeline, report: ScrubReport
    ) -> list[LeafResult]:
        keys = [hashlib.sha256(t.encode()).digest() for t in texts]
        owned: dict[bytes, str] = {}
        for key, text in zip(keys, texts, strict=True):
            if key not in self._leaf_cache and key not in self._pending and key not in owned:
                owned[key] = text
                self._pending[key] = asyncio.get_running_loop().create_future()
        mine = await self._scrub_owned(owned, pipeline, report) if owned else {}
        return [mine[key] if key in mine else await self._result(key) for key in keys]

    async def _scrub_owned(
        self, owned: Mapping[bytes, str], pipeline: Pipeline, report: ScrubReport
    ) -> dict[bytes, LeafResult]:
        try:
            results = await pipeline.scrub(list(owned.values()), self.vault, report)
        except BaseException:
            # Waiters must not hang on a scrub that will never finish; they fail closed instead.
            for key in owned:
                self._settle(key, ScrubFailed("leaf scrub interrupted"))
            raise
        for key, result in zip(owned, results, strict=True):
            self._settle(key, result)
        return dict(zip(owned, results, strict=True))

    def _settle(self, key: bytes, result: LeafResult) -> None:
        future = self._pending.pop(key)
        if isinstance(result, Exception):
            future.set_exception(result)
            future.exception()  # marks it retrieved: having no waiter is normal
        else:
            self._leaf_cache[key] = result
            future.set_result(result)

    async def _result(self, key: bytes) -> LeafResult:
        if key in self._leaf_cache:
            return self._leaf_cache[key]
        try:
            # shield: cancelling one waiter must not cancel the scrub other requests share.
            return await asyncio.shield(self._pending[key])
        except Exception as e:
            return e


class SessionStore:
    def __init__(
        self,
        always_scrub: Mapping[Category, Sequence[str]],
        idle_ttl_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._always_scrub = always_scrub
        self._idle_ttl_s = idle_ttl_s
        self._clock = clock
        self._sessions: dict[str, Session] = {}

    def get(self, session_id: str, *, has_assistant_turns: bool) -> Session:
        now = self._clock()
        # Whole sessions only: dropping part of a leaf cache would change re-sent history bytes.
        for sid in [s for s, v in self._sessions.items() if now - v.last_seen > self._idle_ttl_s]:
            del self._sessions[sid]
        session = self._sessions.get(session_id)
        if session is None:
            vault = Vault.seeded(self._always_scrub)
            session = self._sessions[session_id] = Session(vault, has_assistant_turns, now)
        session.last_seen = now
        return session
