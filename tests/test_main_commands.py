from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import config
import main
from clients.kuluttajaliitto import Statement
from clients.lausuntopalvelu import Proposal


def _setup_state_paths(tmp_path, monkeypatch) -> tuple:
    state_dir = tmp_path / "state"
    context_dir = tmp_path / "context"
    state_dir.mkdir()
    context_dir.mkdir()

    seen_path = state_dir / "seen_proposals.json"
    score_log_path = state_dir / "score_log.jsonl"
    flagged_path = state_dir / "nostetut.json"
    context_path = context_dir / "kuluttajaliitto.json"

    seen_path.write_text("{}", encoding="utf-8")
    score_log_path.write_text("", encoding="utf-8")
    flagged_path.write_text("[]", encoding="utf-8")
    context_path.write_text(
        json.dumps({"last_updated": None, "recent_statements": []}),
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "SEEN_PROPOSALS_PATH", seen_path)
    monkeypatch.setattr(config, "SCORE_LOG_PATH", score_log_path)
    monkeypatch.setattr(config, "FLAGGED_PATH", flagged_path)
    monkeypatch.setattr(config, "CONTEXT_PATH", context_path)
    monkeypatch.setattr(config, "NOTIFY_THRESHOLD", 7)
    monkeypatch.setattr(config, "LOG_THRESHOLD", 4)
    monkeypatch.setattr(config, "LAUSUNTOPALVELU_FETCH_TOP", 5)

    return seen_path, score_log_path, flagged_path, context_path


def test_cmd_daily_no_new_proposals_exits_cleanly(tmp_path, monkeypatch, capsys) -> None:
    seen_path, _score_log_path, _flagged_path, _context_path = _setup_state_paths(
        tmp_path, monkeypatch
    )

    proposal = Proposal(
        id="already-seen",
        title="Jo kasitelty",
        organization_name="Testi",
        abstract="Kuvaus",
        deadline=datetime.now(main.UTC) + timedelta(days=3),
        published_on=datetime.now(main.UTC),
        url="https://example.invalid/p/already-seen",
    )

    seen_path.write_text(
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


def test_cmd_daily_borderline_item_is_logged_but_not_flagged(tmp_path, monkeypatch) -> None:
    seen_path, score_log_path, flagged_path, _context_path = _setup_state_paths(
        tmp_path, monkeypatch
    )

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

    seen = json.loads(seen_path.read_text(encoding="utf-8"))
    assert seen["borderline-1"]["score"] == 5

    flagged = json.loads(flagged_path.read_text(encoding="utf-8"))
    assert flagged == []

    log_lines = [
        line for line in score_log_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(log_lines) == 1
    log_entry = json.loads(log_lines[0])
    assert log_entry["score"] == 5
    assert log_entry["notified"] is False
    assert log_entry["organization"] == "Testi"
    assert log_entry["url"] == "https://example.invalid/p/borderline-1"
    assert log_entry["deadline"] is not None


def test_cmd_daily_borderline_only_triggers_digest(tmp_path, monkeypatch) -> None:
    _setup_state_paths(tmp_path, monkeypatch)

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
    tmp_path, monkeypatch, capsys
) -> None:
    _seen_path, score_log_path, _flagged_path, _context_path = _setup_state_paths(
        tmp_path, monkeypatch
    )
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
    score_log_path.write_text(
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
    monkeypatch.setattr(main, "build_context", lambda statements: {"recent_statements": []})
    monkeypatch.setattr(main, "_save_context", lambda ctx: captured.update({"ctx": ctx}))

    main.cmd_update_context()
    assert captured["ctx"] == {"recent_statements": []}


def test_cmd_daily_handles_scoring_exception(tmp_path, monkeypatch) -> None:
    seen_path, score_log_path, _flagged_path, _context_path = _setup_state_paths(
        tmp_path, monkeypatch
    )

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

    seen = json.loads(seen_path.read_text(encoding="utf-8"))
    assert seen == {}
    assert score_log_path.read_text(encoding="utf-8") == ""


def test_cmd_daily_non_dry_run_sends_email(tmp_path, monkeypatch) -> None:
    _seen_path, _score_log_path, _flagged_path, _context_path = _setup_state_paths(
        tmp_path, monkeypatch
    )
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


def test_cmd_daily_aborts_on_user_no(tmp_path, monkeypatch, capsys) -> None:
    seen_path, score_log_path, flagged_path, _context_path = _setup_state_paths(
        tmp_path, monkeypatch
    )

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
    assert json.loads(seen_path.read_text(encoding="utf-8")) == {}
    assert score_log_path.read_text(encoding="utf-8") == ""
    assert json.loads(flagged_path.read_text(encoding="utf-8")) == []


def test_cmd_daily_dry_run_prints_digest_but_does_not_send(tmp_path, monkeypatch, capsys) -> None:
    seen_path, _score_log_path, _flagged_path, _context_path = _setup_state_paths(
        tmp_path, monkeypatch
    )
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

    seen = json.loads(seen_path.read_text(encoding="utf-8"))
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


def test_cmd_review_logged_no_log_file(tmp_path, monkeypatch, capsys) -> None:
    _seen_path, score_log_path, _flagged_path, _context_path = _setup_state_paths(
        tmp_path, monkeypatch
    )
    score_log_path.unlink()

    main.cmd_review_logged(days=7)
    out = capsys.readouterr().out
    assert "No score log found." in out


def test_cmd_review_logged_only_flagged_in_log_reports_empty(tmp_path, monkeypatch, capsys) -> None:
    _seen_path, score_log_path, _flagged_path, _context_path = _setup_state_paths(
        tmp_path, monkeypatch
    )
    now = datetime.now(main.UTC).isoformat()
    score_log_path.write_text(
        json.dumps({"timestamp": now, "title": "Nostettava", "score": 8, "rationale": "R"}) + "\n",
        encoding="utf-8",
    )

    main.cmd_review_logged(days=7)
    out = capsys.readouterr().out
    assert "No borderline items" in out
    assert "LOGGED" not in out
    assert "Nostettava" not in out


def test_cmd_review_logged_prints_borderline_section(tmp_path, monkeypatch, capsys) -> None:
    _seen_path, score_log_path, _flagged_path, _context_path = _setup_state_paths(
        tmp_path, monkeypatch
    )
    now = datetime.now(main.UTC).isoformat()
    score_log_path.write_text(
        json.dumps({"timestamp": now, "title": "Rajalla", "score": 5, "rationale": "R"}) + "\n",
        encoding="utf-8",
    )

    main.cmd_review_logged(days=7)
    out = capsys.readouterr().out
    assert "LOGGED" in out
    assert "Rajalla" in out


def test_cmd_preview_logged_no_log_file(tmp_path, monkeypatch, capsys) -> None:
    _seen_path, score_log_path, _flagged_path, _context_path = _setup_state_paths(
        tmp_path, monkeypatch
    )
    score_log_path.unlink()

    main.cmd_preview_logged(days=7)
    out = capsys.readouterr().out
    assert "No score log found." in out


def test_cmd_preview_logged_empty_result(tmp_path, monkeypatch, capsys) -> None:
    _seen_path, score_log_path, _flagged_path, _context_path = _setup_state_paths(
        tmp_path, monkeypatch
    )
    now = datetime.now(main.UTC).isoformat()
    # Only items above NOTIFY_THRESHOLD — none in borderline range
    score_log_path.write_text(
        json.dumps({"timestamp": now, "title": "Nostettava", "score": 8, "rationale": "R"}) + "\n",
        encoding="utf-8",
    )

    main.cmd_preview_logged(days=7)
    out = capsys.readouterr().out
    assert "No borderline items" in out


def test_cmd_preview_logged_renders_borderline_items(tmp_path, monkeypatch, capsys) -> None:
    _seen_path, score_log_path, _flagged_path, _context_path = _setup_state_paths(
        tmp_path, monkeypatch
    )
    now = datetime.now(main.UTC)
    entries = [
        {
            "timestamp": now.isoformat(),
            "title": "Rajatapaus",
            "score": 5,
            "rationale": "Ehka kiinnostava",
            "themes": ["kuluttaja"],
            "published_on": now.isoformat(),
            "organization": "Testivirasto",
            "deadline": "2026-05-01",
            "url": "https://example.invalid/p/1",
        },
        {
            "timestamp": (now - timedelta(days=10)).isoformat(),
            "title": "Vanha rajatapaus",
            "score": 4,
            "rationale": "Vanhentunut",
            "themes": [],
            "published_on": now.isoformat(),
        },
    ]
    score_log_path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
        encoding="utf-8",
    )

    main.cmd_preview_logged(days=7)
    out = capsys.readouterr().out
    # Real digest renders the recent borderline item with its details
    assert "Rajatapaus" in out
    assert "Testivirasto" in out
    assert "https://example.invalid/p/1" in out
    # Old item (>7 days) is excluded
    assert "Vanha rajatapaus" not in out


def test_cmd_preview_logged_filters_above_notify_threshold(tmp_path, monkeypatch, capsys) -> None:
    _seen_path, score_log_path, _flagged_path, _context_path = _setup_state_paths(
        tmp_path, monkeypatch
    )
    now = datetime.now(main.UTC).isoformat()
    entries = [
        {"timestamp": now, "title": "Nostettu", "score": 7, "rationale": "R", "themes": []},
        {"timestamp": now, "title": "Rajalla", "score": 5, "rationale": "R", "themes": []},
        {"timestamp": now, "title": "Liian alhainen", "score": 2, "rationale": "R", "themes": []},
    ]
    score_log_path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
        encoding="utf-8",
    )

    main.cmd_preview_logged(days=7)
    out = capsys.readouterr().out
    # Only the borderline item (score 5) reaches the digest; flagged (7) and
    # below-LOG_THRESHOLD (2) are filtered out
    assert "Rajalla" in out
    assert "Nostettu" not in out
    assert "Liian alhainen" not in out


def test_cmd_preview_logged_invalid_dates_still_builds(tmp_path, monkeypatch, capsys) -> None:
    _seen_path, score_log_path, _flagged_path, _context_path = _setup_state_paths(
        tmp_path, monkeypatch
    )
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
    score_log_path.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    captured_items: list = []
    monkeypatch.setattr(
        main,
        "build_daily_digest",
        lambda items: captured_items.extend(items) or ("S", "H", "T"),
    )

    main.cmd_preview_logged(days=7)
    assert len(captured_items) == 1
    assert captured_items[0]["proposal"].published_on is None
    assert captured_items[0]["proposal"].deadline is None


def test_cmd_preview_flagged_invalid_published_on_still_builds(
    tmp_path, monkeypatch, capsys
) -> None:
    _seen_path, _score_log_path, flagged_path, _context_path = _setup_state_paths(
        tmp_path, monkeypatch
    )
    flagged_path.write_text(
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

    captured_items: list = []
    monkeypatch.setattr(
        main,
        "build_daily_digest",
        lambda items: captured_items.extend(items) or ("S", "H", "T"),
    )

    main.cmd_preview_flagged()
    assert len(captured_items) == 1
    assert captured_items[0]["proposal"].published_on is None


def test_cmd_reset_state_clears_files(tmp_path, monkeypatch, capsys) -> None:
    seen_path, score_log_path, flagged_path, _context_path = _setup_state_paths(
        tmp_path, monkeypatch
    )
    monkeypatch.setattr(config, "SEEN_DOCUMENTS_PATH", tmp_path / "state" / "seen_documents.json")
    seen_path.write_text('{"old": true}', encoding="utf-8")
    score_log_path.write_text('{"score": 5}\n', encoding="utf-8")
    flagged_path.write_text('[{"score": 8}]', encoding="utf-8")

    monkeypatch.setattr("builtins.input", lambda _: "y")
    main.cmd_reset_state()

    assert json.loads(seen_path.read_text()) == {}
    assert json.loads(flagged_path.read_text()) == []
    assert score_log_path.read_text() == ""
    assert "State reset." in capsys.readouterr().out


def test_cmd_reset_state_aborts_on_no(tmp_path, monkeypatch, capsys) -> None:
    seen_path, _score_log_path, _flagged_path, _context_path = _setup_state_paths(
        tmp_path, monkeypatch
    )
    seen_path.write_text('{"old": true}', encoding="utf-8")

    monkeypatch.setattr("builtins.input", lambda _: "n")
    main.cmd_reset_state()

    assert json.loads(seen_path.read_text()) == {"old": True}
    assert "Aborted." in capsys.readouterr().out


def test_cmd_preview_flagged_empty_file(tmp_path, monkeypatch, capsys) -> None:
    _seen_path, _score_log_path, flagged_path, _context_path = _setup_state_paths(
        tmp_path, monkeypatch
    )
    flagged_path.write_text("[]", encoding="utf-8")

    main.cmd_preview_flagged()
    out = capsys.readouterr().out
    assert "no flagged items" in out.lower()


def test_cmd_preview_flagged_invalid_deadline_still_builds(tmp_path, monkeypatch, capsys) -> None:
    _seen_path, _score_log_path, flagged_path, _context_path = _setup_state_paths(
        tmp_path, monkeypatch
    )
    flagged_path.write_text(
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

    captured: list = []
    monkeypatch.setattr(
        main,
        "build_daily_digest",
        lambda flagged, borderline=None: captured.extend(flagged) or ("S", "H", "T"),
    )

    main.cmd_preview_flagged()
    # Invalid deadline string is normalized to None before reaching the digest
    assert captured[0]["proposal"].deadline is None


def test_cmd_preview_flagged_missing_deadline_still_builds(tmp_path, monkeypatch, capsys) -> None:
    _seen_path, _score_log_path, flagged_path, _context_path = _setup_state_paths(
        tmp_path, monkeypatch
    )
    flagged_path.write_text(
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

    captured: list = []
    monkeypatch.setattr(
        main,
        "build_daily_digest",
        lambda flagged, borderline=None: captured.extend(flagged) or ("S", "H", "T"),
    )

    main.cmd_preview_flagged()
    # Missing deadline field surfaces as proposal.deadline = None
    assert captured[0]["proposal"].deadline is None
