from __future__ import annotations

import json
from datetime import date, timedelta

import main


def test_ensure_context_fresh_uses_fresh_existing_context(state_paths, monkeypatch) -> None:
    def _should_not_fetch():
        raise AssertionError("fresh context should not refresh")

    monkeypatch.setattr(main, "_fetch_context", _should_not_fetch)

    ctx = main._ensure_context_fresh()

    assert ctx == json.loads(state_paths.context.read_text(encoding="utf-8"))


def test_ensure_context_fresh_refreshes_missing_or_stale_context(state_paths, monkeypatch) -> None:
    state_paths.context.write_text(
        json.dumps(
            {
                "last_updated": (date.today() - timedelta(days=30)).isoformat(),
                "recent_statements": [{"title": "old"}],
            }
        ),
        encoding="utf-8",
    )
    fresh = {"last_updated": date.today().isoformat(), "recent_statements": [{"title": "new"}]}
    monkeypatch.setattr(main, "_fetch_context", lambda: fresh)

    ctx = main._ensure_context_fresh()

    assert ctx == fresh
    assert json.loads(state_paths.context.read_text(encoding="utf-8")) == fresh


def test_ensure_context_fresh_warns_and_uses_existing_context_on_refresh_failure(
    state_paths,
    monkeypatch,
    capsys,
) -> None:
    existing = {
        "last_updated": (date.today() - timedelta(days=30)).isoformat(),
        "recent_statements": [{"title": "old"}],
    }
    state_paths.context.write_text(json.dumps(existing), encoding="utf-8")

    def _raise():
        raise RuntimeError("network down")

    monkeypatch.setattr(main, "_fetch_context", _raise)

    ctx = main._ensure_context_fresh()

    assert ctx == existing
    assert "could not refresh" in capsys.readouterr().err


def test_ensure_context_fresh_aborts_without_usable_context_on_refresh_failure(
    state_paths,
    monkeypatch,
    capsys,
) -> None:
    state_paths.context.write_text(
        json.dumps({"last_updated": None, "recent_statements": []}),
        encoding="utf-8",
    )

    def _raise():
        raise RuntimeError("network down")

    monkeypatch.setattr(main, "_fetch_context", _raise)

    ctx = main._ensure_context_fresh()

    assert ctx is None
    assert "ERROR" in capsys.readouterr().err
