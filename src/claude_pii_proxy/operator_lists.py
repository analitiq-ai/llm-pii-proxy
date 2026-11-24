"""Operator always-scrub / never-scrub lists (FR-018, data-model.md Operator lists).

Error messages name positions, never values: the file holds the operator's own personal data.
"""

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_pii_proxy.vault import Category

_NOT_SCRUBBABLE_BY_OPERATOR = {Category.LITERAL, Category.CONTEXT}
# Claude Code's own vocabulary, which NER tags as PERSON. Anthropic rejects subscription traffic
# whose identity line was altered (research R3).
BUILT_IN_NEVER_SCRUB = frozenset(
    {"Claude", "claude", "Claude Code", "Claude Agent SDK", "Anthropic"}
    | {"Claude Opus", "Claude Sonnet", "Claude Haiku", "Claude Fable"}
)


class OperatorListsError(ValueError):
    pass


@dataclass(frozen=True)
class OperatorLists:
    always_scrub: Mapping[Category, tuple[str, ...]] = field(default_factory=dict)
    never_scrub: frozenset[str] = BUILT_IN_NEVER_SCRUB


def load(path: Path) -> OperatorLists:
    if not path.exists():
        return OperatorLists()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise OperatorListsError(f"{path}: invalid TOML: {e}") from e

    unknown = set(data) - {"always_scrub", "never_scrub"}
    if unknown:
        raise OperatorListsError(f"{path}: unknown keys {sorted(unknown)}")

    never = _strings(data.get("never_scrub", []), f"{path}: never_scrub")
    table = data.get("always_scrub", {})
    if not isinstance(table, dict):
        raise OperatorListsError(f"{path}: always_scrub must be a table")

    always: dict[Category, tuple[str, ...]] = {}
    for name, values in table.items():
        if name not in Category.__members__ or Category[name] in _NOT_SCRUBBABLE_BY_OPERATOR:
            raise OperatorListsError(f"{path}: always_scrub.{name} is not an allowed category")
        where = f"{path}: always_scrub.{name}"
        strings = _strings(values, where)
        for i, value in enumerate(strings):
            if value in never:
                raise OperatorListsError(f"{where}[{i}] is also on never_scrub")
        always[Category[name]] = strings
    return OperatorLists(always_scrub=always, never_scrub=BUILT_IN_NEVER_SCRUB | frozenset(never))


def _strings(values: Any, where: str) -> tuple[str, ...]:
    if not isinstance(values, list):
        raise OperatorListsError(f"{where} must be a list of strings")
    for i, value in enumerate(values):
        if not isinstance(value, str) or not value:
            raise OperatorListsError(f"{where}[{i}] must be a non-empty string")
    return tuple(values)
