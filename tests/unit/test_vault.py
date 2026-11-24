import pytest

from llm_pii_proxy.errors import ScrubFailed
from llm_pii_proxy.vault import TOKEN_PATTERN, Category, Vault, format_token


def test_category_enum_is_closed():
    assert [c.name for c in Category] == [
        "PERSON",
        "EMAIL",
        "PHONE",
        "ADDRESS",
        "CARD",
        "IBAN",
        "NATIONALID",
        "IP",
        "SECRET",
        "CONTEXT",
        "LITERAL",
    ]


def test_token_format_is_fixed_width():
    assert format_token(Category.EMAIL, 1) == "PII_EMAIL_0001"
    assert format_token(Category.NATIONALID, 9999) == "PII_NATIONALID_9999"


def test_assign_in_first_seen_order_per_category():
    v = Vault()
    assert v.assign("a@x.com", Category.EMAIL) == "PII_EMAIL_0001"
    assert v.assign("Margaret", Category.PERSON) == "PII_PERSON_0001"
    assert v.assign("b@x.com", Category.EMAIL) == "PII_EMAIL_0002"


def test_same_value_same_token_first_category_wins():
    v = Vault()
    first = v.assign("Paris Hilton", Category.PERSON)
    assert v.assign("Paris Hilton", Category.PERSON) == first
    assert v.assign("Paris Hilton", Category.ADDRESS) == first
    assert v.assign("Elsewhere", Category.ADDRESS) == "PII_ADDRESS_0001"


def test_by_value_and_by_token_are_inverses():
    v = Vault()
    values = ["a@x.com", "Margaret", "b@x.com", "555-0100"]
    cats = [Category.EMAIL, Category.PERSON, Category.EMAIL, Category.PHONE]
    tokens = [v.assign(val, cat) for val, cat in zip(values, cats, strict=True)]
    for val, tok in zip(values, tokens, strict=True):
        assert v.token_for(val) == tok
        assert v.value_for(tok) == val
    assert dict(v.items()) == dict(zip(values, tokens, strict=True))


def test_value_for_is_case_insensitive():
    v = Vault()
    v.assign("a@x.com", Category.EMAIL)
    assert v.value_for("pii_email_0001") == "a@x.com"
    assert v.value_for("Pii_Email_0001") == "a@x.com"
    assert v.value_for("PII_EMAIL_0002") is None


def test_exhaustion_fails_closed():
    v = Vault()
    for i in range(9999):
        v.assign(f"v{i}", Category.SECRET)
    with pytest.raises(ScrubFailed):
        v.assign("one more", Category.SECRET)
    # other categories are unaffected
    assert v.assign("x@y.z", Category.EMAIL) == "PII_EMAIL_0001"


def test_empty_value_rejected():
    with pytest.raises(ValueError):
        Vault().assign("", Category.PERSON)


def test_append_only_api():
    public = {n for n in dir(Vault) if not n.startswith("_")}
    assert public == {"assign", "value_for", "token_for", "items", "seeded"}


def test_seeding_in_file_order_is_deterministic():
    lists = {Category.PERSON: ["jdoe", "Jane Roe"], Category.EMAIL: ["me@corp.example"]}
    a, b = Vault.seeded(lists), Vault.seeded(lists)
    assert dict(a.items()) == dict(b.items())
    assert a.token_for("jdoe") == "PII_PERSON_0001"
    assert a.token_for("Jane Roe") == "PII_PERSON_0002"
    assert a.token_for("me@corp.example") == "PII_EMAIL_0001"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("see PII_EMAIL_0001 here", ["PII_EMAIL_0001"]),
        ("pii_person_0012", ["pii_person_0012"]),
        ("PII_EMAIL_1", []),
        ("PII_PERSON_00012O3", ["PII_PERSON_0001"]),
        ("PII_TOOLONGCATEGORY_0001", []),
    ],
)
def test_scanning_regex(text, expected):
    assert [m.group(0) for m in TOKEN_PATTERN.finditer(text)] == expected
