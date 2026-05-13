from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import config
import main
from clients.kuluttajaliitto import Statement
from clients.lausuntopalvelu import Proposal


def test_cmd_daily_no_new_proposals_exits_cleanly(state_paths, monkeypatch, capsys) -> None:

    proposal = Proposal(
        id="already-seen",
        title="Jo kasitelty",
        organization_name="Testi",
        abstract="Kuvaus",
        deadline=datetime.now(main.UTC) + timedelta(days=3),
        published_on=datetime.now(main.UTC),
        url="https://example.invalid/p/already-seen",
    )

    state_paths.seen.write_text(
        json.dumps({"already-seen": {"first_seen": "2026-01-01T00:00:00+00:00"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "fetch_recent", lambda client, top: [proposal])

    def _should_not_run(*args, **kwargs):
        raise AssertionError("score_item should not be called when there are no new proposals")

    monkeypatch.setattr(main, "score_item", _should_not_run)
    main.cmd_daily(dry_run=True)
    out = capsys.readouterr().out
    assert "Nothing new to score." in out


def test_cmd_daily_borderline_item_is_logged_but_not_flagged(state_paths, monkeypatch) -> None:

    proposal = Proposal(
        id="borderline-1",
        title="Rajatapaus",
        organization_name="Testi",
        abstract="Kuvaus",
        deadline=datetime.now(main.UTC) + timedelta(days=3),
        published_on=datetime.now(main.UTC),
        url="https://example.invalid/p/borderline-1",
    )

    monkeypatch.setattr(main, "fetch_recent", lambda client, top: [proposal])
    monkeypatch.setattr(main, "get_participation_flags", lambda client, pid, name: (False, False))
    monkeypatch.setattr(
        main,
        "score_item",
        lambda *args, **kwargs: {"score": 5, "rationale": "Rajatapaus", "themes": []},
    )

    main.cmd_daily(dry_run=True)

    seen = json.loads(state_paths.seen.read_text(encoding="utf-8"))
    assert seen["borderline-1"]["score"] == 5

    flagged = json.loads(state_paths.flagged.read_text(encoding="utf-8"))
    assert flagged == []

    log_lines = [
        line
        for line in state_paths.score_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(log_lines) == 1
    log_entry = json.loads(log_lines[0])
    assert log_entry["score"] == 5
    assert log_entry["notified"] is False
    assert log_entry["organization"] == "Testi"
    assert log_entry["url"] == "https://example.invalid/p/borderline-1"
    assert log_entry["deadline"] is not None


def test_cmd_daily_borderline_only_triggers_digest(state_paths, monkeypatch) -> None:

    proposal = Proposal(
        id="borderline-only",
        title="Vain rajatapaus",
        organization_name="Testi",
        abstract="Kuvaus",
        deadline=datetime.now(main.UTC) + timedelta(days=3),
        published_on=datetime.now(main.UTC),
        url="https://example.invalid/p/borderline-only",
    )

    captured: dict = {}

    monkeypatch.setattr(main, "fetch_recent", lambda client, top: [proposal])
    monkeypatch.setattr(main, "get_participation_flags", lambda client, pid, name: (False, False))
    monkeypatch.setattr(
        main,
        "score_item",
        lambda *args, **kwargs: {"score": 5, "rationale": "Rajatapaus", "themes": []},
    )

    def _capture_build(flagged, borderline=None):
        captured["flagged"] = list(flagged)
        captured["borderline"] = list(borderline or [])
        return "SUBJ", "<p>H</p>", "TEXT"

    monkeypatch.setattr(main, "build_daily_digest", _capture_build)

    main.cmd_daily(dry_run=True)

    assert captured["flagged"] == []
    assert len(captured["borderline"]) == 1
    assert captured["borderline"][0]["score"] == 5


def test_cmd_review_logged_shows_borderline_and_excludes_flagged_and_old(
    state_paths, monkeypatch, capsys
) -> None:

    now = datetime.now(main.UTC)

    entries = [
        {
            "timestamp": now.isoformat(),
            "title": "Nostettava",
            "score": 8,
            "rationale": "Selkea",
        },
        {
            "timestamp": now.isoformat(),
            "title": "Rajalla",
            "score": 5,
            "rationale": "Ehka",
        },
        {
            "timestamp": (now - timedelta(days=10)).isoformat(),
            "title": "Vanha rajatapaus",
            "score": 5,
            "rationale": "Vanhentunut",
        },
    ]
    state_paths.score_log.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
        encoding="utf-8",
    )

    main.cmd_review_logged(days=7)
    out = capsys.readouterr().out
    assert "LOGGED" in out
    assert "FLAGGED" not in out
    assert "Rajalla" in out
    assert "Nostettava" not in out
    assert "Vanha rajatapaus" not in out


def test_cmd_update_context_fetches_and_saves(monkeypatch) -> None:
    captured: dict = {}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(main.httpx, "Client", FakeClient)

    def fake_fetch_statements(client, per_page):
        assert per_page == 100
        return [
            Statement(
                id=1,
                date="2026-04-22",
                title="T",
                excerpt="E",
                url="https://example.invalid/1",
            )
        ]

    monkeypatch.setattr(main, "fetch_statements", fake_fetch_statements)
    monkeypatch.setattr(
        main, "build_context", lambda statements: {"recent_statements": [{"title": "T"}]}
    )
    monkeypatch.setattr(
        main, "_load_context", lambda: {"last_updated": None, "recent_statements": []}
    )
    monkeypatch.setattr(main, "_save_context", lambda ctx: captured.update({"ctx": ctx}))

    main.cmd_update_context()
    assert captured["ctx"] == {"recent_statements": [{"title": "T"}]}


def test_cmd_update_context_skips_save_when_unchanged(monkeypatch, capsys) -> None:
    statements = [{"title": "T", "date": "2026-04-22", "excerpt": "E", "url": "u", "tags": []}]

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(main.httpx, "Client", FakeClient)
    monkeypatch.setattr(main, "fetch_statements", lambda client, per_page: [])
    monkeypatch.setattr(main, "build_context", lambda stmts: {"recent_statements": statements})
    monkeypatch.setattr(
        main,
        "_load_context",
        lambda: {"last_updated": "2026-04-22", "recent_statements": statements},
    )

    saved = {"called": False}
    monkeypatch.setattr(main, "_save_context", lambda ctx: saved.__setitem__("called", True))

    main.cmd_update_context()

    assert not saved["called"]
    assert "already up to date" in capsys.readouterr().out


def test_cmd_daily_handles_scoring_exception(state_paths, monkeypatch) -> None:

    proposal = Proposal(
        id="score-fail",
        title="Virhepolku",
        organization_name="Testi",
        abstract="Kuvaus",
        deadline=datetime.now(main.UTC) + timedelta(days=3),
        published_on=datetime.now(main.UTC),
        url="https://example.invalid/p/score-fail",
    )
    monkeypatch.setattr(main, "fetch_recent", lambda client, top: [proposal])
    monkeypatch.setattr(main, "get_participation_flags", lambda client, pid, name: (False, False))

    def _raise_score(*args, **kwargs):
        raise RuntimeError("scoring down")

    monkeypatch.setattr(main, "score_item", _raise_score)

    main.cmd_daily(dry_run=True)

    seen = json.loads(state_paths.seen.read_text(encoding="utf-8"))
    assert seen == {}
    assert state_paths.score_log.read_text(encoding="utf-8") == ""


def test_cmd_daily_non_dry_run_sends_email(state_paths, monkeypatch) -> None:

    calls: dict = {}

    proposal = Proposal(
        id="notify-1",
        title="Nostettava",
        organization_name="Testi",
        abstract="Kuvaus",
        deadline=datetime.now(main.UTC) + timedelta(days=3),
        published_on=datetime.now(main.UTC),
        url="https://example.invalid/p/notify-1",
    )

    monkeypatch.setattr(main, "fetch_recent", lambda client, top: [proposal])
    monkeypatch.setattr(main, "get_participation_flags", lambda client, pid, name: (False, False))
    monkeypatch.setattr(
        main,
        "score_item",
        lambda *args, **kwargs: {"score": 8, "rationale": "OK", "themes": []},
    )
    monkeypatch.setattr(
        main,
        "send_email",
        lambda subject, html_body, text_body: calls.update(
            {"subject": subject, "html": html_body, "text": text_body}
        ),
    )
    main.cmd_daily(dry_run=False)
    # Real digest reaches send_email with the proposal's title in the body
    assert "Nostettava" in calls["text"]
    assert "Nostettava" in calls["html"]
    assert calls["subject"].startswith("Uusia lausuntopyyntöjä")


def test_cmd_daily_aborts_on_user_no(state_paths, monkeypatch, capsys) -> None:

    proposal = Proposal(
        id="abort-1",
        title="Keskeyta",
        organization_name="Testi",
        abstract="Kuvaus",
        deadline=datetime.now(main.UTC) + timedelta(days=3),
        published_on=datetime.now(main.UTC),
        url="https://example.invalid/p/abort-1",
    )

    monkeypatch.setattr(main, "fetch_recent", lambda client, top: [proposal])
    monkeypatch.setattr("builtins.input", lambda _: "n")

    def _should_not_score(*args, **kwargs):
        raise AssertionError("score_item should not run after user abort")

    monkeypatch.setattr(main, "score_item", _should_not_score)

    main.cmd_daily(dry_run=True)
    out = capsys.readouterr().out
    assert "Aborted." in out
    assert json.loads(state_paths.seen.read_text(encoding="utf-8")) == {}
    assert state_paths.score_log.read_text(encoding="utf-8") == ""
    assert json.loads(state_paths.flagged.read_text(encoding="utf-8")) == []


def test_cmd_daily_dry_run_prints_digest_but_does_not_send(
    state_paths, monkeypatch, capsys
) -> None:

    proposal = Proposal(
        id="dryrun-1",
        title="Dryrun nostettava",
        organization_name="Testi",
        abstract="Kuvaus",
        deadline=datetime.now(main.UTC) + timedelta(days=3),
        published_on=datetime.now(main.UTC),
        url="https://example.invalid/p/dryrun-1",
    )

    monkeypatch.setattr(main, "fetch_recent", lambda client, top: [proposal])
    monkeypatch.setattr(main, "get_participation_flags", lambda client, pid, name: (False, False))
    monkeypatch.setattr(
        main,
        "score_item",
        lambda *args, **kwargs: {"score": 8, "rationale": "OK", "themes": []},
    )

    def _should_not_send(*args, **kwargs):
        raise AssertionError("send_email should not run in dry-run mode")

    monkeypatch.setattr(main, "send_email", _should_not_send)

    main.cmd_daily(dry_run=True)
    out = capsys.readouterr().out
    # Real digest is printed (contains the proposal title) but email is not sent
    assert "Dryrun nostettava" in out
    assert "--- DRY RUN: would send email ---" in out

    seen = json.loads(state_paths.seen.read_text(encoding="utf-8"))
    assert seen["dryrun-1"]["notified"] is False


def test_deliver_digest_aborts_when_send_declined(monkeypatch, capsys) -> None:
    monkeypatch.setattr("builtins.input", lambda _: "n")

    sent = {"called": False}
    flagged = [
        {
            "proposal": SimpleNamespace(
                title="T", organization_name="O", published_on=None, deadline=None, url=""
            ),
            "score": 7,
            "rationale": "R",
            "themes": [],
        }
    ]
    monkeypatch.setattr(
        main, "build_daily_digest", lambda f, borderline=None: ("S", "<p>H</p>", "Body")
    )
    monkeypatch.setattr(
        main, "send_email", lambda subject, html_body, text_body: sent.__setitem__("called", True)
    )

    main._deliver_digest(flagged, dry_run=False)

    out = capsys.readouterr().out
    assert "Aborted." in out
    assert not sent["called"]


def test_cmd_review_logged_no_log_file(state_paths, capsys) -> None:

    state_paths.score_log.unlink()

    main.cmd_review_logged(days=7)
    out = capsys.readouterr().out
    assert "No borderline items" in out


def test_cmd_review_logged_only_flagged_in_log_reports_empty(
    state_paths, monkeypatch, capsys
) -> None:

    now = datetime.now(main.UTC).isoformat()
    state_paths.score_log.write_text(
        json.dumps({"timestamp": now, "title": "Nostettava", "score": 8, "rationale": "R"}) + "\n",
        encoding="utf-8",
    )

    main.cmd_review_logged(days=7)
    out = capsys.readouterr().out
    assert "No borderline items" in out
    assert "LOGGED" not in out
    assert "Nostettava" not in out


def test_cmd_review_logged_prints_borderline_section(state_paths, monkeypatch, capsys) -> None:

    now = datetime.now(main.UTC).isoformat()
    state_paths.score_log.write_text(
        json.dumps({"timestamp": now, "title": "Rajalla", "score": 5, "rationale": "R"}) + "\n",
        encoding="utf-8",
    )

    main.cmd_review_logged(days=7)
    out = capsys.readouterr().out
    assert "LOGGED" in out
    assert "Rajalla" in out


def test_cmd_preview_digest_no_content(state_paths, capsys) -> None:
    # Both stores empty (fixture default)
    main.cmd_preview_digest()
    out = capsys.readouterr().out
    assert "nothing to preview" in out.lower()


def test_cmd_preview_digest_renders_borderline_from_log(state_paths, capsys) -> None:
    now = datetime.now(main.UTC)
    deadline = (date.today() + timedelta(days=14)).isoformat()
    entries = [
        {
            "timestamp": now.isoformat(),
            "title": "Rajatapaus",
            "score": 5,
            "rationale": "Ehka kiinnostava",
            "themes": ["kuluttaja"],
            "published_on": now.isoformat(),
            "organization": "Testivirasto",
            "deadline": deadline,
            "url": "https://example.invalid/p/1",
        },
        {
            "timestamp": (now - timedelta(days=10)).isoformat(),
            "title": "Vanha rajatapaus",
            "score": 4,
            "rationale": "Vanhentunut",
            "themes": [],
        },
    ]
    state_paths.score_log.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
        encoding="utf-8",
    )

    main.cmd_preview_digest(days=7)
    out = capsys.readouterr().out
    assert "Rajatapauksia" in out  # borderline section header
    assert "Rajatapaus" in out
    assert "Testivirasto" in out
    assert "https://example.invalid/p/1" in out
    assert "Vanha rajatapaus" not in out  # outside 7-day window


def test_cmd_preview_digest_filters_score_thresholds(state_paths, capsys) -> None:
    now = datetime.now(main.UTC).isoformat()
    entries = [
        {"timestamp": now, "title": "Nostettu", "score": 7, "rationale": "R", "themes": []},
        {"timestamp": now, "title": "Rajalla", "score": 5, "rationale": "R", "themes": []},
        {"timestamp": now, "title": "Liian alhainen", "score": 2, "rationale": "R", "themes": []},
    ]
    state_paths.score_log.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
        encoding="utf-8",
    )

    main.cmd_preview_digest(days=7)
    out = capsys.readouterr().out
    assert "Rajalla" in out
    assert "Nostettu" not in out
    assert "Liian alhainen" not in out


def test_load_borderline_invalid_dates_normalize_to_none(state_paths) -> None:
    now = datetime.now(main.UTC).isoformat()
    entry = {
        "timestamp": now,
        "title": "Virheelliset päivämäärät",
        "score": 5,
        "rationale": "R",
        "themes": [],
        "published_on": "not-a-date",
        "deadline": "also-not-a-date",
    }
    state_paths.score_log.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    items = main._load_borderline(days=7)
    assert len(items) == 1
    assert items[0]["proposal"].published_on is None
    assert items[0]["proposal"].deadline is None


def test_load_flagged_invalid_published_on_normalizes_to_none(state_paths) -> None:
    state_paths.flagged.write_text(
        json.dumps(
            [
                {
                    "title": "Aihe",
                    "organization": "Org",
                    "published_on": "not-a-date",
                    "url": "https://example.invalid/p/1",
                    "score": 7,
                    "rationale": "R",
                    "themes": [],
                }
            ]
        ),
        encoding="utf-8",
    )

    items = main._load_flagged()
    assert len(items) == 1
    assert items[0]["proposal"].published_on is None


def test_cmd_reset_state_clears_files(state_paths, monkeypatch, capsys) -> None:

    monkeypatch.setattr(
        config, "SEEN_DOCUMENTS_PATH", state_paths.seen.parent / "seen_documents.json"
    )
    state_paths.seen.write_text('{"old": true}', encoding="utf-8")
    state_paths.score_log.write_text('{"score": 5}\n', encoding="utf-8")
    state_paths.flagged.write_text('[{"score": 8}]', encoding="utf-8")

    monkeypatch.setattr("builtins.input", lambda _: "y")
    main.cmd_reset_state()

    assert json.loads(state_paths.seen.read_text()) == {}
    assert json.loads(state_paths.flagged.read_text()) == []
    assert state_paths.score_log.read_text() == ""
    assert "State reset." in capsys.readouterr().out


def test_cmd_reset_state_aborts_on_no(state_paths, monkeypatch, capsys) -> None:

    state_paths.seen.write_text('{"old": true}', encoding="utf-8")

    monkeypatch.setattr("builtins.input", lambda _: "n")
    main.cmd_reset_state()

    assert json.loads(state_paths.seen.read_text()) == {"old": True}
    assert "Aborted." in capsys.readouterr().out


def test_load_flagged_invalid_deadline_normalizes_to_none(state_paths) -> None:
    state_paths.flagged.write_text(
        json.dumps(
            [
                {
                    "title": "Aihe",
                    "organization": None,
                    "deadline": "invalid-date",
                    "url": "https://example.invalid/p/1",
                    "score": 7,
                    "rationale": "R",
                    "themes": ["t"],
                }
            ]
        ),
        encoding="utf-8",
    )

    items = main._load_flagged()
    # Invalid deadline string is normalized to None (item kept since expiry unknown)
    assert items[0]["proposal"].deadline is None


def test_load_flagged_missing_deadline_normalizes_to_none(state_paths) -> None:
    state_paths.flagged.write_text(
        json.dumps(
            [
                {
                    "title": "Aihe ilman deadlinea",
                    "organization": "Org",
                    "url": "https://example.invalid/p/2",
                    "score": 7,
                    "rationale": "R",
                    "themes": ["t"],
                }
            ]
        ),
        encoding="utf-8",
    )

    items = main._load_flagged()
    assert items[0]["proposal"].deadline is None


def test_load_flagged_excludes_expired_items(state_paths) -> None:
    today = date.today()
    yesterday = (today - timedelta(days=1)).isoformat()
    tomorrow = (today + timedelta(days=1)).isoformat()

    state_paths.flagged.write_text(
        json.dumps(
            [
                {
                    "title": "Vanhentunut",
                    "organization": "Org",
                    "deadline": yesterday,
                    "url": "https://example.invalid/expired",
                    "score": 7,
                    "rationale": "R",
                    "themes": [],
                },
                {
                    "title": "Voimassa",
                    "organization": "Org",
                    "deadline": tomorrow,
                    "url": "https://example.invalid/open",
                    "score": 7,
                    "rationale": "R",
                    "themes": [],
                },
                {
                    "title": "Ei deadlinea",
                    "organization": "Org",
                    "url": "https://example.invalid/no-deadline",
                    "score": 7,
                    "rationale": "R",
                    "themes": [],
                },
            ]
        ),
        encoding="utf-8",
    )

    result = main._load_flagged()
    titles = [item["proposal"].title for item in result]

    assert "Vanhentunut" not in titles
    assert "Voimassa" in titles
    assert "Ei deadlinea" in titles
