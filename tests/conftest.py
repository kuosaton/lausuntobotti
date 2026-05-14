from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import NamedTuple

import pytest

import config


@pytest.fixture(autouse=True)
def auto_confirm(monkeypatch):
    def _input(prompt):
        return "0" if prompt.strip() == ">" else "y"

    monkeypatch.setattr("builtins.input", _input)


class StatePaths(NamedTuple):
    seen: Path
    score_log: Path
    valiokunta_score_log: Path
    score_log_migration_marker: Path
    flagged: Path
    context: Path


@pytest.fixture
def state_paths(tmp_path, monkeypatch) -> StatePaths:
    """Create empty state and context files in tmp_path and point config at them.

    Also pins the scoring thresholds (FLAG_THRESHOLD, LOG_THRESHOLD) and the
    fetch top (5) so tests don't depend on production values. The context starts
    fresh and non-empty so command tests do not accidentally exercise refresh
    behavior unless they opt into it.
    """
    state_dir = tmp_path / "state"
    context_dir = tmp_path / "context"
    state_dir.mkdir()
    context_dir.mkdir()

    paths = StatePaths(
        seen=state_dir / "seen_proposals.json",
        score_log=state_dir / "score_log.jsonl",
        valiokunta_score_log=state_dir / "valiokunta_score_log.jsonl",
        score_log_migration_marker=state_dir / ".score_log_split_migrated",
        flagged=state_dir / "nostetut.json",
        context=context_dir / "kuluttajaliitto.json",
    )

    paths.seen.write_text("{}", encoding="utf-8")
    paths.score_log.write_text("", encoding="utf-8")
    paths.valiokunta_score_log.write_text("", encoding="utf-8")
    paths.flagged.write_text("[]", encoding="utf-8")
    paths.context.write_text(
        json.dumps(
            {"last_updated": date.today().isoformat(), "recent_statements": [{"title": "x"}]}
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "SEEN_PROPOSALS_PATH", paths.seen)
    monkeypatch.setattr(config, "LAUSUNTOPALVELU_SCORE_LOG_PATH", paths.score_log)
    monkeypatch.setattr(config, "SCORE_LOG_PATH", paths.score_log)
    monkeypatch.setattr(config, "VALIOKUNTA_SCORE_LOG_PATH", paths.valiokunta_score_log)
    monkeypatch.setattr(
        config, "SCORE_LOG_SPLIT_MIGRATION_MARKER", paths.score_log_migration_marker
    )
    monkeypatch.setattr(config, "FLAGGED_PATH", paths.flagged)
    monkeypatch.setattr(config, "CONTEXT_PATH", paths.context)
    monkeypatch.setattr(config, "CONTEXT_MAX_AGE_DAYS", 7)
    monkeypatch.setattr(config, "FLAG_THRESHOLD", 6)
    monkeypatch.setattr(config, "LOG_THRESHOLD", 4)
    monkeypatch.setattr(config, "LAUSUNTOPALVELU_FETCH_TOP", 5)

    return paths
