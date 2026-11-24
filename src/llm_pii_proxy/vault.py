"""Per-session mapping between original values and placeholders (data-model.md, Vault)."""

import re
from collections.abc import Iterator, Mapping, Sequence
from enum import StrEnum
from typing import Self

from llm_pii_proxy.errors import ScrubFailed


class Category(StrEnum):
    PERSON = "PERSON"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    ADDRESS = "ADDRESS"
    CARD = "CARD"
    IBAN = "IBAN"
    NATIONALID = "NATIONALID"
    IP = "IP"
    SECRET = "SECRET"
    CONTEXT = "CONTEXT"
    LITERAL = "LITERAL"


TOKEN_PATTERN = re.compile(r"PII_([A-Z]{1,10})_([0-9]{4})", re.IGNORECASE)
MAX_ORDINAL = 9999


def format_token(category: Category, ordinal: int) -> str:
    return f"PII_{category}_{ordinal:04d}"


class Vault:
    """Append-only: an entry is never changed or removed while its session lives."""

    def __init__(self) -> None:
        self._by_value: dict[str, str] = {}
        self._by_token: dict[str, str] = {}
        self._last_ordinal: dict[Category, int] = {}

    @classmethod
    def seeded(cls, always_scrub: Mapping[Category, Sequence[str]]) -> Self:
        vault = cls()
        for category, values in always_scrub.items():
            for value in values:
                vault.assign(value, category)
        return vault

    def assign(self, value: str, category: Category) -> str:
        if not value:
            raise ValueError("empty value")
        if (token := self._by_value.get(value)) is not None:
            return token
        ordinal = self._last_ordinal.get(category, 0) + 1
        if ordinal > MAX_ORDINAL:
            raise ScrubFailed(f"placeholder numbers exhausted for category {category}")
        token = format_token(category, ordinal)
        self._last_ordinal[category] = ordinal
        self._by_value[value] = token
        self._by_token[token] = value
        return token

    def token_for(self, value: str) -> str | None:
        return self._by_value.get(value)

    def value_for(self, token: str) -> str | None:
        return self._by_token.get(token.upper())

    def items(self) -> Iterator[tuple[str, str]]:
        """(value, token) pairs in assignment order."""
        return iter(self._by_value.items())
