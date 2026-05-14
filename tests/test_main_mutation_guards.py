from __future__ import annotations

import json
from datetime import datetime, timedelta

import config
import main
import state_store
import workflows.lausuntopyynnot as lausunto_workflow
from clients.lausuntopalvelu import Proposal


def _setup_paths(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    context_dir = tmp_path / "context"
    state_dir.mkdir()
    context_dir.mkdir()

    seen_path = state_dir / "seen_proposals.json"
    seen_docs_path = state_dir / "seen_documents.json"
    score_log_path = state_dir / "score_log.jsonl"
    flagged_path = state_dir / "nostetut.json"
    context_path = context_dir / "kuluttajaliitto.json"

    seen_path.write_text("{}", encoding="utf-8")
    seen_docs_path.write_text("{}", encoding="utf-8")
    score_log_path.write_text("", encoding="utf-8")
    flagged_path.write_text("[]", encoding="utf-8")
    context_path.write_text(
        json.dumps({"last_updated": None, "recent_statements": []}),
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "SEEN_PROPOSALS_PATH", seen_path)
    monkeypatch.setattr(config, "SEEN_DOCUMENTS_PATH", seen_docs_path)
    monkeypatch.setattr(config, "SCORE_LOG_PATH", score_log_path)
    monkeypatch.setattr(config, "FLAGGED_PATH", flagged_path)
    monkeypatch.setattr(config, "CONTEXT_PATH", context_path)

    return seen_path, seen_docs_path, score_log_path, flagged_path, context_path


def test_load_json_missing_and_size_boundaries(tmp_path) -> None:
    missing = tmp_path / "missing.json"
    assert state_store._load_json(missing) == {}

    two_chars = tmp_path / "two_chars.json"
    two_chars.write_text("[]", encoding="utf-8")
    assert state_store._load_json(two_chars) == {}

    three_chars = tmp_path / "three_chars.json"
    three_chars.write_text("42 ", encoding="utf-8")
    assert state_store._load_json(three_chars) == 42


def test_save_json_writes_pretty_utf8_json(tmp_path) -> None:
    path = tmp_path / "saved.json"

    state_store._save_json(path, {"nimi": "Ääkkönen", "score": 7})

    content = path.read_text(encoding="utf-8")
    assert '"nimi": "Ääkkönen"' in content
    assert "\\u00" not in content
    assert content.startswith("{\n  ")
    assert not path.with_suffix(".tmp").exists()


def test_append_log_writes_jsonl_with_newline(tmp_path, monkeypatch) -> None:
    _seen_path, _seen_docs_path, score_log_path, _flagged_path, _context_path = _setup_paths(
        tmp_path, monkeypatch
    )

    state_store._append_log({"title": "Ää", "score": 5})
    state_store._append_log({"title": "Toinen", "score": 8})

    raw = score_log_path.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert "Ää" in raw

    lines = [line for line in raw.splitlines() if line.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0])["score"] == 5
    assert json.loads(lines[1])["score"] == 8


def test_append_flagged_appends_existing_items(tmp_path, monkeypatch) -> None:
    _seen_path, _seen_docs_path, _score_log_path, flagged_path, _context_path = _setup_paths(
        tmp_path, monkeypatch
    )
    flagged_path.write_text(
        json.dumps([{"id": "a", "score": 7}], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    state_store._append_flagged({"id": "b", "score": 8})

    items = json.loads(flagged_path.read_text(encoding="utf-8"))
    assert [item["id"] for item in items] == ["a", "b"]


def test_load_context_boundary_and_valid_content(tmp_path, monkeypatch) -> None:
    _seen_path, _seen_docs_path, _score_log_path, _flagged_path, context_path = _setup_paths(
        tmp_path, monkeypatch
    )

    context_path.write_text("[]", encoding="utf-8")
    assert main._load_context() == {"last_updated": None, "recent_statements": []}

    expected = {
        "last_updated": "2026-04-01",
        "recent_statements": [{"date": "2026-04-01", "title": "T"}],
    }
    context_path.write_text(json.dumps(expected, ensure_ascii=False), encoding="utf-8")
    assert main._load_context() == expected


def test_record_result_populates_seen_and_log_payload(tmp_path, monkeypatch) -> None:
    _seen_path, _seen_docs_path, _score_log_path, _flagged_path, _context_path = _setup_paths(
        tmp_path, monkeypatch
    )

    captured_logs: list[dict] = []

    def _capture_log(entry: dict) -> None:
        captured_logs.append(entry)

    monkeypatch.setattr(lausunto_workflow, "_append_log", _capture_log)

    class FixedDateTime:
        @staticmethod
        def now(tz):
            assert tz is main.UTC
            return datetime(2026, 1, 2, 3, 4, 5, tzinfo=main.UTC)

    monkeypatch.setattr(lausunto_workflow, "datetime", FixedDateTime)

    proposal = Proposal(
        id="proposal-1",
        title="Test title",
        organization_name="Org",
        abstract="A",
        deadline=datetime.now(main.UTC) + timedelta(days=1),
        published_on=datetime.now(main.UTC),
        url="https://example.invalid/p/1",
    )
    seen: dict = {}
    result = {
        "score": 6,
        "rationale": "Perustelu",
        "themes": ["teema"],
        "jakelu_kuluttajaliitto": False,
    }

    lausunto_workflow._record_result(proposal, result, notified=True, seen=seen)

    assert seen[proposal.id]["first_seen"] == "2026-01-02T03:04:05+00:00"
    assert seen[proposal.id]["score"] == 6
    assert seen[proposal.id]["notified"] is True
    assert seen[proposal.id]["notified_at"] == "2026-01-02T03:04:05+00:00"

    assert len(captured_logs) == 1
    log_entry = captured_logs[0]
    assert log_entry["timestamp"] == "2026-01-02T03:04:05+00:00"
    assert log_entry["id"] == proposal.id
    assert log_entry["title"] == proposal.title
    assert log_entry["score"] == 6
    assert log_entry["rationale"] == "Perustelu"
    assert "XXrationaleXX" not in log_entry
    assert log_entry["themes"] == ["teema"]
    assert log_entry["jakelu_kuluttajaliitto"] is False
    assert log_entry["notified"] is True
