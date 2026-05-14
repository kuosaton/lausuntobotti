from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import main
from clients.lausuntopalvelu import Proposal


def test_load_context_defaults_when_missing(state_paths) -> None:
    state_paths.context.unlink()

    ctx = main._load_context()
    assert ctx == {"last_updated": None, "recent_statements": []}


def test_save_context_writes_json(state_paths) -> None:
    payload = {"last_updated": "2026-04-22", "recent_statements": [{"title": "A"}]}

    main._save_context(payload)
    stored = json.loads(state_paths.context.read_text(encoding="utf-8"))
    assert stored == payload


def test_cmd_daily_warns_if_distribution_lookup_fails_and_drops_low_score(
    state_paths, monkeypatch, capsys
) -> None:
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
    assert state_paths.score_log.read_text(encoding="utf-8").strip() != ""


def test_cmd_review_logged_skips_blank_and_invalid_json(state_paths, capsys) -> None:
    now = datetime.now(main.UTC).isoformat()
    state_paths.score_log.write_text(
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


def test_cmd_review_logged_skips_missing_and_invalid_timestamps(state_paths, capsys) -> None:
    state_paths.score_log.write_text(
        "\n".join(
            [
                json.dumps({"title": "Missing timestamp", "score": 5, "rationale": "R"}),
                json.dumps(
                    {
                        "timestamp": "not-a-date",
                        "title": "Invalid timestamp",
                        "score": 5,
                        "rationale": "R",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    main.cmd_review_logged(days=7)
    out = capsys.readouterr().out
    assert "No borderline items" in out
    assert "Missing timestamp" not in out
    assert "Invalid timestamp" not in out


def test_cmd_preview_digest_empty(state_paths, capsys) -> None:
    state_paths.flagged.write_text("[ ]", encoding="utf-8")
    # score log is empty from fixture — no borderline either

    main.cmd_preview_digest()
    out = capsys.readouterr().out
    assert "nothing to preview" in out.lower()


def test_cmd_preview_digest_valid_deadline_parsed(state_paths, monkeypatch) -> None:
    state_paths.flagged.write_text(
        json.dumps(
            [
                {
                    "title": "Aihe",
                    "organization": "Org",
                    "published_on": "2026-04-19T09:30:00",
                    "deadline": (date.today() + timedelta(days=30)).isoformat(),
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
    main.cmd_preview_digest()
    # Valid ISO date strings are parsed into datetime objects
    assert captured[0]["proposal"].deadline is not None
    assert captured[0]["proposal"].published_on is not None
