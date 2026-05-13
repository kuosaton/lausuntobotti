from __future__ import annotations

import json
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
    flagged: Path
    context: Path


@pytest.fixture
def state_paths(tmp_path, monkeypatch) -> StatePaths:
    """Create empty state and context files in tmp_path and point config at them.

    Also pins the scoring thresholds (NOTIFY_THRESHOLD=7, LOG_THRESHOLD=4) and the
    fetch top (5) so tests don't depend on production values. The context starts
    with an empty recent_statements list — tests that need a non-empty context can
    overwrite the file directly via the returned context path.
    """
    state_dir = tmp_path / "state"
    context_dir = tmp_path / "context"
    state_dir.mkdir()
    context_dir.mkdir()

    paths = StatePaths(
        seen=state_dir / "seen_proposals.json",
        score_log=state_dir / "score_log.jsonl",
        flagged=state_dir / "nostetut.json",
        context=context_dir / "kuluttajaliitto.json",
    )

    paths.seen.write_text("{}", encoding="utf-8")
    paths.score_log.write_text("", encoding="utf-8")
    paths.flagged.write_text("[]", encoding="utf-8")
    paths.context.write_text(
        json.dumps({"last_updated": None, "recent_statements": []}),
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "SEEN_PROPOSALS_PATH", paths.seen)
    monkeypatch.setattr(config, "SCORE_LOG_PATH", paths.score_log)
    monkeypatch.setattr(config, "FLAGGED_PATH", paths.flagged)
    monkeypatch.setattr(config, "CONTEXT_PATH", paths.context)
    monkeypatch.setattr(config, "NOTIFY_THRESHOLD", 7)
    monkeypatch.setattr(config, "LOG_THRESHOLD", 4)
    monkeypatch.setattr(config, "LAUSUNTOPALVELU_FETCH_TOP", 5)

    return paths
