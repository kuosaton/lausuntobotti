"""End-to-end tests for cmd_lausuntopyynnot.

These tests exercise the full pipeline — fetch → score → flag/log → render → send —
with only the external boundaries stubbed (HTTP, LLM, Resend). Internal helpers like
build_lausuntopyynto_digest and _append_flagged run for real, so a regression in any handoff
between them (field renames, dropped keys, format changes) is caught here.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import resend

import main
import workflows.lausuntopyynnot as lausunto_workflow
from clients.lausuntopalvelu import Proposal


def test_cmd_lausuntopyynnot_full_pipeline_renders_real_digest(state_paths, monkeypatch) -> None:
    """Exercise fetch → score → render → send with real digest building.

    Only stubs HTTP, LLM, Resend, and the prompts. Asserts that the email
    actually sent by Resend contains the proposal data — title, score,
    organization, URL, both rationale and themes.
    """
    del state_paths  # fixture pins config paths; we don't read any back

    monkeypatch.setenv("SENDER_EMAIL", "botti@example.com")
    monkeypatch.setenv("RECIPIENT_EMAIL", "vastaanottaja@example.com")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")

    flag_deadline = datetime.now(main.UTC) + timedelta(days=14)
    border_deadline = datetime.now(main.UTC) + timedelta(days=21)
    published = datetime.now(main.UTC) - timedelta(days=2)

    flagged_proposal = Proposal(
        id="flagged-e2e",
        title="Asumisturvalaki",
        organization_name="Ympäristöministeriö",
        abstract="Esitys koskien asumisturvaa.",
        deadline=flag_deadline,
        published_on=published,
        url="https://example.invalid/p/flagged-e2e",
    )
    borderline_proposal = Proposal(
        id="border-e2e",
        title="Tuotemerkintä-asetus",
        organization_name="TEM",
        abstract="Tuotemerkintöjä koskeva esitys.",
        deadline=border_deadline,
        published_on=published,
        url="https://example.invalid/p/border-e2e",
    )
    dropped_proposal = Proposal(
        id="drop-e2e",
        title="Asia ilman kuluttajakytkentää",
        organization_name="OM",
        abstract="Ei kosketa kuluttajia.",
        deadline=flag_deadline,
        published_on=published,
        url="https://example.invalid/p/drop-e2e",
    )

    scores_by_id = {
        "flagged-e2e": {
            "score": 9,
            "rationale": "Suora kuluttajavaikutus.",
            "themes": ["asuminen", "kuluttajansuoja"],
        },
        "border-e2e": {
            "score": 5,
            "rationale": "Vain välillinen kuluttajakytkentä.",
            "themes": ["tuoteturvallisuus"],
        },
        "drop-e2e": {
            "score": 1,
            "rationale": "Ei kuluttajakytkentää.",
            "themes": [],
        },
    }

    monkeypatch.setattr(
        lausunto_workflow,
        "fetch_recent",
        lambda client, top: [flagged_proposal, borderline_proposal, dropped_proposal],
    )
    monkeypatch.setattr(
        lausunto_workflow, "get_participation_flags", lambda client, pid, name: (False, False)
    )
    monkeypatch.setattr(
        lausunto_workflow,
        "score_item",
        lambda title, abstract, source, ctx: dict(scores_by_id[_find_id_by_title(title)]),
    )
    monkeypatch.setattr("builtins.input", lambda prompt: "y")

    captured_email: dict = {}

    def fake_send(params):
        captured_email.update(params)
        return {"id": "fake-id"}

    monkeypatch.setattr(resend.Emails, "send", staticmethod(fake_send))

    main.cmd_lausuntopyynnot(dry_run=False)

    # Email was actually sent
    assert captured_email["from"] == "botti@example.com"
    assert captured_email["to"] == ["vastaanottaja@example.com"]

    # Subject is the date-stamped lausuntopyyntö form
    today = date.today()
    assert captured_email["subject"] == (
        f"Uusia lausuntopyyntöjä, {today.day}.{today.month}.{today.year}"
    )

    text = captured_email["text"]
    html = captured_email["html"]

    # Flagged proposal appears with its full detail
    assert "Asumisturvalaki" in text
    assert "[9/10] Asumisturvalaki" in text
    assert "Ympäristöministeriö" in text
    assert "Suora kuluttajavaikutus." in text
    assert "asuminen, kuluttajansuoja" in text
    assert "https://example.invalid/p/flagged-e2e" in text
    assert "Asumisturvalaki" in html

    # Borderline section is present and below the flagged one
    assert "Rajatapauksia" in text
    assert text.index("Asumisturvalaki") < text.index("Rajatapauksia")
    assert text.index("Rajatapauksia") < text.index("Tuotemerkintä-asetus")
    assert "Tuotemerkintä-asetus" in html

    # Dropped item does NOT appear
    assert "Asia ilman kuluttajakytkentää" not in text
    assert "drop-e2e" not in text


def test_cmd_lausuntopyynnot_persists_flagged_with_complete_shape(state_paths, monkeypatch) -> None:
    """End-to-end: cmd_lausuntopyynnot writes the full flagged record to nostetut.json.

    This is the contract used by cmd_resend_digest / cmd_preview_digest when they
    re-read flagged items off disk, so every field must round-trip.
    """

    deadline = datetime.now(main.UTC) + timedelta(days=10)
    published = datetime.now(main.UTC) - timedelta(days=1)
    proposal = Proposal(
        id="persist-1",
        title="Pysyvä asia",
        organization_name="STM",
        abstract="Tiivistelmä",
        deadline=deadline,
        published_on=published,
        url="https://example.invalid/p/persist-1",
    )

    monkeypatch.setattr(lausunto_workflow, "fetch_recent", lambda client, top: [proposal])
    monkeypatch.setattr(
        lausunto_workflow, "get_participation_flags", lambda client, pid, name: (False, False)
    )
    monkeypatch.setattr(
        lausunto_workflow,
        "score_item",
        lambda *args, **kwargs: {
            "score": 8,
            "rationale": "Kuluttajavaikutus.",
            "themes": ["terveys"],
        },
    )
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    monkeypatch.setattr(resend.Emails, "send", staticmethod(lambda p: {"id": "ok"}))
    monkeypatch.setenv("SENDER_EMAIL", "x@example.com")
    monkeypatch.setenv("RECIPIENT_EMAIL", "y@example.com")

    main.cmd_lausuntopyynnot(dry_run=False)

    flagged_records = json.loads(state_paths.flagged.read_text(encoding="utf-8"))
    assert len(flagged_records) == 1
    record = flagged_records[0]

    # Every field cmd_resend_digest / cmd_preview_digest relies on must be present
    expected_keys = {
        "timestamp",
        "source",
        "id",
        "title",
        "score",
        "rationale",
        "themes",
        "jakelu_kuluttajaliitto",
        "published_on",
        "deadline",
        "organization",
        "url",
    }
    assert expected_keys.issubset(record.keys()), f"Missing keys: {expected_keys - record.keys()}"

    assert record["id"] == "persist-1"
    assert record["title"] == "Pysyvä asia"
    assert record["score"] == 8
    assert record["rationale"] == "Kuluttajavaikutus."
    assert record["themes"] == ["terveys"]
    assert record["source"] == "lausuntopalvelu"
    assert record["jakelu_kuluttajaliitto"] is False
    assert record["organization"] == "STM"
    assert record["url"] == "https://example.invalid/p/persist-1"
    assert record["deadline"] == deadline.date().isoformat()
    assert record["published_on"] == published.isoformat()


def _find_id_by_title(title: str) -> str:
    """Map a proposal title back to its id (only used by the fake score_item)."""
    mapping = {
        "Asumisturvalaki": "flagged-e2e",
        "Tuotemerkintä-asetus": "border-e2e",
        "Asia ilman kuluttajakytkentää": "drop-e2e",
    }
    return mapping[title]
