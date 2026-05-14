from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import config

_SOURCE_LAUSUNTOPYYNNOT = "lausuntopyynnot"
_SOURCE_VALIOKUNTA = "valiokunta"
_VALIOKUNTA_SOURCE_PREFIXES = (*config.COMMITTEE_URLS.keys(), _SOURCE_VALIOKUNTA)


def _read_json[T](path: Path, default: T) -> T:
    if path.exists() and path.stat().st_size > 2:
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def _load_json(path: Path) -> dict[str, Any]:
    return _read_json(path, {})


def _write_json_atomic(path: Path, data: object) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _save_json(path: Path, data: dict[str, Any]) -> None:
    _write_json_atomic(path, data)


def _append_log(entry: dict[str, Any], path: Path | None = None) -> None:
    path = path or config.SCORE_LOG_PATH
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _write_jsonl(path: Path, entries: Iterable[dict[str, Any] | str]) -> None:
    lines = []
    for entry in entries:
        if isinstance(entry, str):
            lines.append(entry.rstrip("\n"))
        else:
            lines.append(json.dumps(entry, ensure_ascii=False))
    path.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")


def _score_log_path(source: str) -> Path:
    if source == _SOURCE_LAUSUNTOPYYNNOT:
        return config.LAUSUNTOPALVELU_SCORE_LOG_PATH
    if source == _SOURCE_VALIOKUNTA:
        return config.VALIOKUNTA_SCORE_LOG_PATH
    raise ValueError(f"Unknown score log source: {source!r}")


def _is_valiokunta_source(source: object) -> bool:
    return isinstance(source, str) and source in _VALIOKUNTA_SOURCE_PREFIXES


def _migrate_score_log_split() -> None:
    marker = config.SCORE_LOG_SPLIT_MIGRATION_MARKER
    if marker.exists():
        return
    source_path = config.LAUSUNTOPALVELU_SCORE_LOG_PATH
    if not source_path.exists():
        marker.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
        return

    lausuntopyynnot: list[dict[str, Any] | str] = []
    valiokunta: list[dict[str, Any]] = []
    for line in source_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
        except json.JSONDecodeError:
            lausuntopyynnot.append(line)
            continue
        if _is_valiokunta_source(entry.get("source")):
            valiokunta.append(entry)
        else:
            lausuntopyynnot.append(entry)

    existing_valiokunta = []
    valiokunta_path = config.VALIOKUNTA_SCORE_LOG_PATH
    if valiokunta_path.exists() and valiokunta_path.stat().st_size > 0:
        existing_valiokunta = [
            line
            for line in valiokunta_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    _write_jsonl(source_path, lausuntopyynnot)
    _write_jsonl(valiokunta_path, [*existing_valiokunta, *valiokunta])
    marker.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")


def _append_flagged(entry: dict[str, Any]) -> None:
    path = config.FLAGGED_PATH
    items = _read_json(path, [])
    items.append(entry)
    _write_json_atomic(path, items)


def _load_context() -> dict[str, Any]:
    return _read_json(config.CONTEXT_PATH, {"last_updated": None, "recent_statements": []})


def _save_context(ctx: dict[str, Any]) -> None:
    _write_json_atomic(config.CONTEXT_PATH, ctx)
