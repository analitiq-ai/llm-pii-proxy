"""Generate the labelled local-detection corpus: `uv run python tests/fixtures/make_corpus.py`.

Writes tests/fixtures/corpus/local.jsonl, one record per line: {"context", "text", "spans"},
each span {"start", "end", "category"} over `text`. Fixed seeds and the Faker version pinned in
uv.lock make the output reproducible; it is committed so the labels can be reviewed.

Stripe and Slack keys are left out: secret scanning on push flags their formats, so they are covered
by the concatenated samples in tests/integration/test_secret_detector.py instead.
"""

import json
import random
import string
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from faker import Faker

from claude_pii_proxy.vault import Category

OUT = Path(__file__).parent / "corpus" / "local.jsonl"
SEED = 20261123
RECORDS_PER_CONTEXT = 60


@dataclass
class Doc:
    context: str
    parts: list[str] = field(default_factory=list)
    spans: list[dict] = field(default_factory=list)
    length: int = 0

    def text(self, s: str) -> "Doc":
        self.parts.append(s)
        self.length += len(s)
        return self

    def value(self, s: str, category: Category) -> "Doc":
        self.spans.append({"start": self.length, "end": self.length + len(s), "category": category})
        return self.text(s)

    def record(self) -> dict:
        return {"context": self.context, "text": "".join(self.parts), "spans": self.spans}


class Values:
    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.us = Faker("en_US")
        self.gb = Faker("en_GB")
        self.us.seed_instance(rng.randrange(2**32))
        self.gb.seed_instance(rng.randrange(2**32))

    def faker(self) -> Faker:
        return self.rng.choice([self.us, self.gb])

    def name(self) -> str:
        # Not name(): it adds titles and suffixes ("Dr", "MD") that are not part of the value.
        f = self.faker()
        return f"{f.first_name()} {f.last_name()}"

    def given_name(self) -> str:
        return self.faker().first_name()

    def email(self) -> str:
        return self.faker().email()

    def phone(self) -> str:
        r = self.rng
        area = r.choice(["212", "415", "646", "312", "617", "303"])
        return r.choice(
            [
                f"+1 {area}-555-{r.randrange(100, 200):04d}",
                f"({area}) 555-{r.randrange(100, 200):04d}",
                f"+44 20 7946 {r.randrange(0, 1000):04d}",
                f"020 7946 {r.randrange(0, 1000):04d}",
            ]
        )

    def address(self) -> str:
        f = self.us
        return (
            f"{f.building_number()} {f.street_name()}, {f.city()}, {f.state_abbr()} {f.zipcode()}"
        )

    def card(self) -> str:
        number = self.us.credit_card_number(card_type=self.rng.choice(["visa16", "mastercard"]))
        return " ".join(number[i : i + 4] for i in range(0, 16, 4))

    def iban(self) -> str:
        return self.faker().iban()

    def ssn(self) -> str:
        return self.us.ssn()

    def nhs(self) -> str:
        while True:
            digits = [self.rng.randrange(10) for _ in range(9)]
            check = 11 - sum(d * w for d, w in zip(digits, range(10, 1, -1), strict=True)) % 11
            if check == 11:
                check = 0
            if check != 10:
                n = "".join(map(str, digits)) + str(check)
                return f"{n[:3]} {n[3:6]} {n[6:]}"

    def national_id(self) -> str:
        return self.rng.choice([self.ssn, self.nhs])()

    def ip(self) -> str:
        return self.rng.choice([self.us.ipv4_public, self.us.ipv6])()

    def _token(self, alphabet: str, n: int) -> str:
        return "".join(self.rng.choice(alphabet) for _ in range(n))

    def aws_key(self) -> str:
        return "AKIA" + self._token(string.ascii_uppercase + "234567", 16)

    def github_token(self) -> str:
        return "ghp_" + self._token(string.ascii_letters + string.digits, 36)

    def password(self) -> str:
        middle = self._token(string.ascii_letters + string.digits, 8)
        return f"{self.us.word().capitalize()}-{middle}-{self.rng.randrange(10, 99)}"

    def private_key(self) -> str:
        body = "\n".join(
            self._token(string.ascii_letters + string.digits + "+/", 64) for _ in range(4)
        )
        return f"-----BEGIN RSA PRIVATE KEY-----\n{body}\n-----END RSA PRIVATE KEY-----"


def prose(v: Values) -> Doc:
    d = Doc("prose")
    who, given = v.name(), v.given_name()
    d.text("Hi team, ").value(who, Category.PERSON).text(" called about the invoice. Reach her at ")
    d.value(v.phone(), Category.PHONE).text(" or ").value(v.email(), Category.EMAIL).text(".\n")
    d.text("Ship the replacement to ").value(v.address(), Category.ADDRESS).text(" by Friday.\n")
    d.text("Her national insurance record lists ").value(v.national_id(), Category.NATIONALID)
    d.text(". Thanks, ").value(given, Category.PERSON).text("\n")
    return d


def csv(v: Values) -> Doc:
    d = Doc("csv")
    d.text("id,name,email,phone,address,card,iban,national_id,ip\n")
    for i in range(1, 4):
        d.text(f"{i},").value(v.name(), Category.PERSON).text(",")
        d.value(v.email(), Category.EMAIL).text(",").value(v.phone(), Category.PHONE).text(',"')
        d.value(v.address(), Category.ADDRESS).text('",').value(v.card(), Category.CARD).text(",")
        d.value(v.iban(), Category.IBAN).text(",").value(v.national_id(), Category.NATIONALID)
        d.text(",").value(v.ip(), Category.IP).text("\n")
    return d


def log(v: Values) -> Doc:
    d = Doc("log")
    d.text("2026-11-02T09:14:07Z INFO auth login ok user=").value(v.email(), Category.EMAIL)
    d.text(" src=").value(v.ip(), Category.IP).text("\n")
    d.text("2026-11-02T09:14:09Z WARN billing card declined card=").value(v.card(), Category.CARD)
    d.text(' holder="').value(v.name(), Category.PERSON).text('"\n')
    d.text("2026-11-02T09:15:31Z INFO payout queued iban=").value(v.iban(), Category.IBAN)
    d.text(" callback=").value(v.phone(), Category.PHONE).text("\n")
    return d


def code(v: Values) -> Doc:
    d = Doc("code")
    d.text('AWS_ACCESS_KEY_ID = "').value(v.aws_key(), Category.SECRET).text('"\n')
    d.text("GITHUB_TOKEN=").value(v.github_token(), Category.SECRET).text("\n")
    d.text('settings = {"owner": "').value(v.name(), Category.PERSON).text('", "password": "')
    d.value(v.password(), Category.SECRET).text('"}\n')
    d.text("DATABASE_URL=postgres://app:").value(v.password(), Category.SECRET)
    d.text("@db.internal:5432/billing\n")
    d.text('ALLOWED_HOSTS = ["').value(v.ip(), Category.IP).text('"]\n')
    if v.rng.random() < 0.3:
        d.text("SIGNING_KEY = '''").value(v.private_key(), Category.SECRET).text("'''\n")
    return d


CONTEXTS: list[Callable[[Values], Doc]] = [prose, csv, log, code]


def generate() -> list[dict]:
    values = Values(random.Random(SEED))
    return [make(values).record() for make in CONTEXTS for _ in range(RECORDS_PER_CONTEXT)]


def main() -> None:
    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w") as f:
        for record in generate():
            f.write(json.dumps(record) + "\n")


if __name__ == "__main__":
    main()
