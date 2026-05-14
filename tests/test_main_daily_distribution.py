from __future__ import annotations

import json
from datetime import datetime, timedelta

import config
import main
import workflows.lausuntopyynnot as lausunto_workflow
from clients.lausuntopalvelu import Proposal


def test_cmd_daily_skips_when_kuluttajaliitto_already_on_distribution_list(
    tmp_path, monkeypatch
) -> None:
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

    proposal = Proposal(
        id="test-proposal-id",
        title="Lakimuutos kuluttajille",
        organization_name="Testiministerio",
        abstract="Kuvaus",
        deadline=datetime.now(main.UTC) + timedelta(days=5),
        published_on=datetime.now(main.UTC),
        url="https://example.invalid/proposal/test-proposal-id",
    )

    monkeypatch.setattr(lausunto_workflow, "fetch_recent", lambda client, top: [proposal])

    captured_lookup = {"calls": 0}

    def fake_flags(client, pid, name):
        captured_lookup["calls"] += 1
        captured_lookup["pid"] = pid
        captured_lookup["name"] = name
        return True, False

    monkeypatch.setattr(lausunto_workflow, "get_participation_flags", fake_flags)

    def should_not_score(*args, **kwargs):
        raise AssertionError("score_item should not run for distribution-list-skipped proposals")

    monkeypatch.setattr(lausunto_workflow, "score_item", should_not_score)

    main.cmd_daily(dry_run=True)
    main.cmd_daily(dry_run=True)

    assert captured_lookup == {"calls": 1, "pid": proposal.id, "name": "Kuluttajaliit"}

    seen = json.loads(seen_path.read_text(encoding="utf-8"))
    assert seen[proposal.id]["status"] == "skipped_jakelu"
    assert seen[proposal.id]["notified"] is False

    log_lines = [
        line for line in score_log_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(log_lines) == 0

    flagged = json.loads(flagged_path.read_text(encoding="utf-8"))
    assert flagged == []


def test_cmd_daily_skips_when_kuluttajaliitto_already_responded(tmp_path, monkeypatch) -> None:
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

    proposal = Proposal(
        id="responded-proposal-id",
        title="Jo vastattu lausuntopyyntö",
        organization_name="Testiministerio",
        abstract="Kuvaus",
        deadline=datetime.now(main.UTC) + timedelta(days=5),
        published_on=datetime.now(main.UTC),
        url="https://example.invalid/proposal/responded-proposal-id",
    )

    monkeypatch.setattr(lausunto_workflow, "fetch_recent", lambda client, top: [proposal])
    monkeypatch.setattr(
        lausunto_workflow, "get_participation_flags", lambda client, pid, name: (False, True)
    )

    def should_not_score(*args, **kwargs):
        raise AssertionError("score_item should not run for already-responded proposals")

    monkeypatch.setattr(lausunto_workflow, "score_item", should_not_score)

    main.cmd_daily(dry_run=True)

    seen = json.loads(seen_path.read_text(encoding="utf-8"))
    assert seen[proposal.id]["status"] == "skipped_already_responded"
    assert seen[proposal.id]["notified"] is False
    assert score_log_path.read_text(encoding="utf-8") == ""
    assert json.loads(flagged_path.read_text(encoding="utf-8")) == []
