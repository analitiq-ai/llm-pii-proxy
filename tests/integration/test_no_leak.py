"""No planted value leaves the machine (SC-001, US1 scenarios 1-4, FR-009, FR-018).

Real detectors: Presidio with spaCy, and the gitleaks secret detector.
"""

import csv
import io
import json
import logging
import re

import httpx
import pytest

from llm_pii_proxy.detection.gitleaks import SecretDetector
from llm_pii_proxy.detection.presidio import PresidioDetector
from llm_pii_proxy.vault import Category
from tests.conftest import FIXTURES
from tests.integration.conftest import SESSION, sent

PROJECT = FIXTURES / "pii-project"
PLANTED = (FIXTURES / "pii-project.planted.txt").read_text().splitlines()
FILES = {name: (PROJECT / name).read_text() for name in ("customers.csv", ".env", "CLAUDE.md")}
USERNAME = "qvellmoran"  # must not be the real user: logs may hold the real home path
TOKEN = re.compile(r"PII_[A-Z]+_[0-9]{4}")


@pytest.fixture
async def proxy(make_proxy, presidio_engine, secret_detector_binary):
    secrets = SecretDetector(secret_detector_binary)
    await secrets.start()
    yield make_proxy(
        [PresidioDetector(presidio_engine, min_score=0.4), secrets],
        always_scrub={Category.PERSON: (USERNAME,)},
    )
    await secrets.close()


def tool_use(i, name, tool_input):
    return {"type": "tool_use", "id": f"toolu_{i}", "name": name, "input": tool_input}


def response(*blocks):
    return httpx.Response(
        200,
        json={
            "id": "msg",
            "type": "message",
            "role": "assistant",
            "content": list(blocks),
            "stop_reason": "end_turn",
        },
    )


def conversation():
    """Three requests; files arrive as tool results in the second and third."""
    system = [{"type": "text", "text": f"Working directory: /home/{USERNAME}/brightwater"}]
    reads = [
        tool_use(i, "Read", {"file_path": f"/home/{USERNAME}/brightwater/{name}"})
        for i, name in enumerate(FILES)
    ]
    results = [
        {"type": "tool_result", "tool_use_id": f"toolu_{i}", "content": text}
        for i, text in enumerate(FILES.values())
    ]
    first = [{"role": "user", "content": "Who is our first customer, and what is the DB password?"}]
    second = [*first, {"role": "assistant", "content": reads}, {"role": "user", "content": results}]
    third = [
        *second,
        {"role": "assistant", "content": [{"type": "text", "text": "Done."}]},
        {
            "role": "user",
            "content": "Add a note to CLAUDE.md under the maintainer line, then email "
            "margaret.ellsworth@brightwaterdental.co.uk.",
        },
    ]
    return [
        {"model": "m", "max_tokens": 100, "system": system, "messages": m}
        for m in (first, second, third)
    ]


def scrubbed_file(body, name):
    results = body["messages"][2]["content"]
    return results[list(FILES).index(name)]["content"]


async def test_no_planted_value_upstream_and_responses_restored(proxy, upstream, caplog, capsys):
    logging.getLogger().setLevel(logging.DEBUG)
    caplog.set_level(logging.DEBUG)
    route = upstream.post("/v1/messages")
    route.mock(return_value=response({"type": "text", "text": "Reading files."}))
    bodies = conversation()

    await proxy.post("/v1/messages", json=bodies[0], headers=SESSION)
    await proxy.post("/v1/messages", json=bodies[1], headers=SESSION)
    second = sent(upstream)

    # Tokens the model would use, read off the scrubbed files it was sent.
    row = next(csv.DictReader(io.StringIO(scrubbed_file(second, "customers.csv"))))
    email_token, name_token = row["email"], row["name"]
    env = dict(line.split("=", 1) for line in scrubbed_file(second, ".env").splitlines())
    aws_token = env["AWS_ACCESS_KEY_ID"]
    maintainer_line = scrubbed_file(second, "CLAUDE.md").splitlines()[2]
    assert all(TOKEN.fullmatch(t) for t in (email_token, name_token, aws_token))

    route.mock(
        return_value=response(
            {"type": "text", "text": f"First customer: {name_token} <{email_token}>."},
            tool_use(
                9,
                "Edit",
                {
                    "file_path": f"/home/{USERNAME}/brightwater/.env",
                    "old_string": f"AWS_ACCESS_KEY_ID={aws_token}",
                    "new_string": f"AWS_ACCESS_KEY_ID={aws_token}\nAWS_REGION=eu-west-2",
                },
            ),
            tool_use(
                10,
                "Edit",
                {
                    "file_path": f"/home/{USERNAME}/brightwater/CLAUDE.md",
                    "old_string": maintainer_line,
                    "new_string": maintainer_line + "\nNote: invoices run monthly.",
                },
            ),
        )
    )
    r = await proxy.post("/v1/messages", json=bodies[2], headers=SESSION)
    third = sent(upstream)

    # SC-001 and FR-018: nothing planted and not the username goes upstream, in any request.
    for call in upstream.calls:
        raw = call.request.content.decode()
        for value in [*PLANTED, USERNAME]:
            assert value not in raw and json.dumps(value)[1:-1] not in raw, value
    # Scenario 4: same value, same token, every turn.
    assert scrubbed_file(third, "customers.csv") == scrubbed_file(second, "customers.csv")
    assert email_token in third["messages"][-1]["content"]
    assert third["system"] == second["system"]
    assert re.search(r"/home/PII_PERSON_[0-9]{4}/", second["system"][0]["text"])

    content = r.json()["content"]
    # Scenario 1: tokens in the answer come back as the real values.
    assert (
        content[0]["text"]
        == "First customer: Margaret Ellsworth <margaret.ellsworth@brightwaterdental.co.uk>."
    )
    # Scenario 3: the Edit tool input holds the real key.
    assert content[1]["input"]["old_string"] == "AWS_ACCESS_KEY_ID=AKIAZ3MHN4QWRT6YB5UX"
    assert content[1]["input"]["file_path"] == f"/home/{USERNAME}/brightwater/.env"
    # Scenario 2: editing next to the maintainer line keeps its name and phone byte-identical.
    original_line = FILES["CLAUDE.md"].splitlines()[2]
    assert content[2]["input"]["old_string"] == original_line
    assert content[2]["input"]["new_string"].startswith(original_line + "\n")

    # FR-009: no value in any log record or on stderr, even at DEBUG.
    logged = caplog.text + capsys.readouterr().err
    for value in [*PLANTED, USERNAME]:
        assert value not in logged, value
