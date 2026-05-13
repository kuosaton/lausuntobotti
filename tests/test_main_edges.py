from __future__ import annotations

import json
from datetime import datetime, timedelta

import config
import main
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


def test_load_context_defaults_when_missing(tmp_path, monkeypatch) -> None:
    _seen_path, _score_log_path, _flagged_path, context_path = _setup_state_paths(
        tmp_path, monkeypatch
    )
    context_path.unlink()

    ctx = main._load_context()
    assert ctx == {"last_updated": None, "recent_statements": []}


def test_save_context_writes_json(tmp_path, monkeypatch) -> None:
    _seen_path, _score_log_path, _flagged_path, context_path = _setup_state_paths(
        tmp_path, monkeypatch
    )
    payload = {"last_updated": "2026-04-22", "recent_statements": [{"title": "A"}]}

    main._save_context(payload)
    stored = json.loads(context_path.read_text(encoding="utf-8"))
    assert stored == payload


def test_cmd_daily_warns_if_distribution_lookup_fails_and_drops_low_score(
    tmp_path, monkeypatch, capsys
) -> None:
    _seen_path, score_log_path, _flagged_path, _context_path = _setup_state_paths(
        tmp_path, monkeypatch
    )
    proposal = Proposal(
        id="drop-1",
        title="Putoaa",
        organization_name="Testi",
        abstract="Kuvaus",
        deadline=datetime.now(main.UTC) + timedelta(days=2),
        published_on=datetime.now(main.UTC),
        url="https://example.invalid/p/drop-1",
    )

    monkeypatch.setattr(main, "fetch_recent", lambda client, top: [proposal])

    def _raise_distribution(*args, **kwargs):
        raise main.httpx.HTTPError("distribution lookup unavailable")

    monkeypatch.setattr(main, "get_participation_flags", _raise_distribution)
    monkeypatch.setattr(
        main,
        "score_item",
        lambda *args, **kwargs: {"score": 2, "rationale": "Ei relevanssia", "themes": []},
    )

    main.cmd_daily(dry_run=True)
    captured = capsys.readouterr()
    assert "[WARN] could not read participation info" in captured.err
    assert "[DROP 2/10]" in captured.out
    assert score_log_path.read_text(encoding="utf-8").strip() != ""


def test_cmd_review_logged_skips_blank_and_invalid_json(tmp_path, monkeypatch, capsys) -> None:
    _seen_path, score_log_path, _flagged_path, _context_path = _setup_state_paths(
        tmp_path, monkeypatch
    )
    now = datetime.now(main.UTC).isoformat()
    score_log_path.write_text(
        "\n".join(
            [
                "",
                "not-json",
                json.dumps({"timestamp": now, "title": "Low", "score": 1, "rationale": "R"}),
            ]
        ),
        encoding="utf-8",
    )

    main.cmd_review_logged(days=7)
    out = capsys.readouterr().out
    assert "No borderline items" in out


def test_cmd_preview_flagged_empty_list_branch(tmp_path, monkeypatch, capsys) -> None:
    _seen_path, _score_log_path, flagged_path, _context_path = _setup_state_paths(
        tmp_path, monkeypatch
    )
    flagged_path.write_text("[ ]", encoding="utf-8")

    main.cmd_preview_flagged()
    out = capsys.readouterr().out
    assert "no flagged items" in out.lower()


def test_cmd_preview_flagged_valid_deadline_branch(tmp_path, monkeypatch, capsys) -> None:
    _seen_path, _score_log_path, flagged_path, _context_path = _setup_state_paths(
        tmp_path, monkeypatch
    )
    flagged_path.write_text(
        json.dumps(
            [
                {
                    "title": "Aihe",
                    "organization": "Org",
                    "published_on": "2026-04-19T09:30:00",
                    "deadline": "2026-05-12",
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
    # Valid ISO date strings are parsed into datetime objects
    assert captured[0]["proposal"].deadline is not None
    assert captured[0]["proposal"].published_on is not None
