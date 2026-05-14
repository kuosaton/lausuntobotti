from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import resend

import delivery.email as email_mod


def test_send_email_uses_resend(monkeypatch) -> None:

    captured: dict = {}

    def fake_send(params):
        captured.update(params)
        return {"id": "fake-id-123"}

    monkeypatch.setattr(resend.Emails, "send", staticmethod(fake_send))
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("SENDER_EMAIL", "botti@example.com")
    monkeypatch.setenv("RECIPIENT_EMAIL", "vastaanottaja@example.com")

    email_id = email_mod.send_email(subject="Testisubject", html_body="<p>Hei</p>", text_body="Hei")

    assert email_id == "fake-id-123"
    assert resend.api_key == "re_test_key"
    assert captured["from"] == "botti@example.com"
    assert captured["to"] == ["vastaanottaja@example.com"]
    assert captured["subject"] == "Testisubject"
    assert captured["html"] == "<p>Hei</p>"
    assert captured["text"] == "Hei"


def test_send_email_requires_configuration(monkeypatch) -> None:
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("SENDER_EMAIL", raising=False)
    monkeypatch.delenv("RECIPIENT_EMAIL", raising=False)

    try:
        email_mod.send_email(subject="S", html_body="<p>H</p>", text_body="T")
    except ValueError as exc:
        assert "RESEND_API_KEY" in str(exc)
    else:
        raise AssertionError("send_email should reject missing API key")

    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    try:
        email_mod.send_email(subject="S", html_body="<p>H</p>", text_body="T")
    except ValueError as exc:
        assert "SENDER_EMAIL" in str(exc)
    else:
        raise AssertionError("send_email should reject missing sender")

    monkeypatch.setenv("SENDER_EMAIL", "botti@example.com")
    try:
        email_mod.send_email(subject="S", html_body="<p>H</p>", text_body="T")
    except ValueError as exc:
        assert "RECIPIENT_EMAIL" in str(exc)
    else:
        raise AssertionError("send_email should reject missing recipients")


def test_send_email_propagates_provider_failure(monkeypatch) -> None:
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("SENDER_EMAIL", "botti@example.com")
    monkeypatch.setenv("RECIPIENT_EMAIL", "vastaanottaja@example.com")

    def fake_send(_params):
        raise RuntimeError("provider down")

    monkeypatch.setattr(resend.Emails, "send", staticmethod(fake_send))

    try:
        email_mod.send_email(subject="S", html_body="<p>H</p>", text_body="T")
    except RuntimeError as exc:
        assert "provider down" in str(exc)
    else:
        raise AssertionError("send_email should propagate provider failure")


def test_build_daily_digest_contains_key_fields() -> None:
    published = date.today() - timedelta(days=3)
    deadline = date.today() + timedelta(days=17)
    flagged = [
        {
            "proposal": SimpleNamespace(
                title="Asumista koskeva luonnos",
                organization_name="Ympäristöministeriö",
                published_on=datetime.combine(published, datetime.min.time()),
                deadline=datetime.combine(deadline, datetime.min.time()),
                url="https://example.invalid/proposal/1",
            ),
            "score": 8,
            "rationale": "Selkeä kuluttajavaikutus.",
            "themes": ["asuminen", "kuluttajansuoja"],
        }
    ]

    subject, html_body, text_body = email_mod.build_daily_digest(flagged)
    published_str = f"{published.day}.{published.month}.{published.year}"
    deadline_str = f"{deadline.day}.{deadline.month}.{deadline.year}"
    assert "Uusia lausuntopyyntöjä" in subject
    assert "pistemäärä 8" in text_body  # score range in header
    assert "[8/10] Asumista koskeva luonnos" in text_body  # score on title line
    assert "Relevanssi" not in text_body  # no longer a separate field
    assert f"Julkaistu: {published_str}" in text_body
    assert "https://example.invalid/proposal/1" in text_body
    assert deadline_str in text_body
    assert "pv" in text_body
    assert "Teemat:    asuminen, kuluttajansuoja" in text_body
    assert "─" in text_body  # separator present
    assert "Julkaistu" in html_body
    assert "Teemat: asuminen, kuluttajansuoja" in html_body
    assert "pv" in html_body


def test_build_daily_digest_sorts_by_score_descending() -> None:
    def _proposal(title: str) -> SimpleNamespace:
        return SimpleNamespace(
            title=title,
            organization_name="Org",
            published_on=datetime(2026, 4, 1),
            deadline=datetime(2026, 5, 30),
            url="https://example.invalid/p/1",
        )

    flagged = [
        {"proposal": _proposal("Matala"), "score": 6, "rationale": "R", "themes": []},
        {"proposal": _proposal("Korkea"), "score": 9, "rationale": "R", "themes": []},
        {"proposal": _proposal("Keski"), "score": 7, "rationale": "R", "themes": []},
    ]

    _, _, text_body = email_mod.build_daily_digest(flagged)
    pos_korkea = text_body.index("Korkea")
    pos_keski = text_body.index("Keski")
    pos_matala = text_body.index("Matala")
    assert pos_korkea < pos_keski < pos_matala


def test_build_daily_digest_sorts_by_deadline_within_same_score() -> None:
    def _proposal(title: str, deadline: datetime | None) -> SimpleNamespace:
        return SimpleNamespace(
            title=title,
            organization_name="Org",
            published_on=datetime(2026, 4, 1),
            deadline=deadline,
            url="https://example.invalid/p/1",
        )

    flagged = [
        {
            "proposal": _proposal("Kiireeton", datetime(2026, 6, 30)),
            "score": 7,
            "rationale": "R",
            "themes": [],
        },
        {
            "proposal": _proposal("Kiireinen", datetime(2026, 5, 2)),
            "score": 7,
            "rationale": "R",
            "themes": [],
        },
        {"proposal": _proposal("EiDeadlinea", None), "score": 7, "rationale": "R", "themes": []},
    ]

    _, _, text_body = email_mod.build_daily_digest(flagged)
    pos_kiireinen = text_body.index("Kiireinen")
    pos_kiireeton = text_body.index("Kiireeton")
    pos_ei = text_body.index("EiDeadlinea")
    assert pos_kiireinen < pos_kiireeton < pos_ei


def test_build_daily_digest_deadline_today() -> None:
    proposal = SimpleNamespace(
        title="T",
        organization_name="Org",
        published_on=datetime(2026, 4, 1),
        deadline=datetime.combine(date.today(), datetime.min.time()),
        url="https://example.invalid/p/1",
    )
    _, _, text_body = email_mod.build_daily_digest(
        [{"proposal": proposal, "score": 7, "rationale": "R", "themes": []}]
    )
    assert "tänään" in text_body


def test_build_daily_digest_omits_url_line_when_empty() -> None:
    proposal = SimpleNamespace(
        title="Ei urlia",
        organization_name="Org",
        published_on=datetime(2026, 4, 1),
        deadline=None,
        url="",
    )
    _, _, text_body = email_mod.build_daily_digest(
        [{"proposal": proposal, "score": 7, "rationale": "R", "themes": []}]
    )
    lines = text_body.splitlines()
    assert not any(line.strip() == "" and line.startswith("   ") for line in lines)


def _digest_item(title: str, score: int) -> dict:
    return {
        "proposal": SimpleNamespace(
            title=title,
            organization_name="Org",
            published_on=datetime(2026, 4, 1),
            deadline=None,
            url=f"https://example.invalid/{title}",
        ),
        "score": score,
        "rationale": "R",
        "themes": [],
    }


def test_build_daily_digest_renders_both_sections_in_order() -> None:
    flagged = [_digest_item("Nostettava", 8)]
    borderline = [_digest_item("Rajatapaus", 5)]

    _, html_body, text_body = email_mod.build_daily_digest(flagged, borderline)

    assert "Rajatapauksia" in text_body
    assert text_body.index("Nostettava") < text_body.index("Rajatapauksia")
    assert text_body.index("Rajatapauksia") < text_body.index("Rajatapaus")
    assert "Rajatapauksia" in html_body
    assert html_body.index("Nostettava") < html_body.index("Rajatapauksia")


def test_build_daily_digest_borderline_only_still_renders() -> None:
    borderline = [_digest_item("Vain rajatapaus", 4)]

    subject, html_body, text_body = email_mod.build_daily_digest([], borderline)

    assert "Uusia lausuntopyyntöjä" in subject
    assert "Rajatapauksia" in text_body
    assert "Vain rajatapaus" in text_body
    assert "Uusia lausuntopyyntöjä" not in text_body  # flagged section header absent
    assert "Vain rajatapaus" in html_body


def test_build_daily_digest_flagged_only_omits_borderline_header() -> None:
    flagged = [_digest_item("Vain nostettava", 8)]

    _, html_body, text_body = email_mod.build_daily_digest(flagged)

    assert "Vain nostettava" in text_body
    assert "Rajatapauksia" not in text_body
    assert "Rajatapauksia" not in html_body


def test_build_weekly_digest_handles_empty_and_linked_items() -> None:
    committee_items = {
        "talousvaliokunta": [
            {
                "eduskuntatunnus": "TaVE 1/2026 vp",
                "title": "HE 1/2026 vp",
                "score": 7,
                "rationale": "Merkittävä kuluttajavaikutus.",
                "themes": ["kuluttajansuoja"],
                "url": "https://example.invalid/doc/1",
            }
        ],
        "ymparistovaliokunta": [],
    }

    subject, html_body, text_body = email_mod.build_weekly_digest(
        committee_items=committee_items,
        week_number=17,
        total_scored=9,
        total_logged=2,
    )

    assert "vko 17" in subject
    assert "TALOUSVALIOKUNTA" in text_body
    assert "Ei nostettavia asioita." in text_body
    assert "Arvioitu yhteensä: 9 asiaa" in text_body
    assert "https://example.invalid/doc/1" in html_body


def test_build_weekly_digest_renders_borderline_items() -> None:
    committee_items = {"talousvaliokunta": []}
    borderline_items = {
        "talousvaliokunta": [
            {
                "eduskuntatunnus": "HE 2/2026 vp",
                "title": "Rajatapaus",
                "score": 5,
                "rationale": "Välillinen kuluttajavaikutus.",
                "themes": ["energia"],
                "url": "",
            }
        ]
    }

    _subject, html_body, text_body = email_mod.build_weekly_digest(
        committee_items=committee_items,
        week_number=17,
        total_scored=1,
        total_logged=1,
        borderline_items=borderline_items,
    )

    assert "Rajatapauksia" in text_body
    assert "[5/10] Rajatapaus" in text_body
    assert "HE 2/2026 vp" in text_body
    assert "Ei nostettavia asioita." not in text_body
    assert "Rajatapaus" in html_body
    assert "energia" in html_body
