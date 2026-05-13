from __future__ import annotations

import json
from datetime import datetime

import main


def _line(entry: dict) -> str:
    return json.dumps(entry, ensure_ascii=False)


def test_score_log_split_migration_moves_valiokunta_entries(state_paths) -> None:
    now = datetime.now(main.UTC).isoformat()
    lausunto = {"timestamp": now, "source": "lausuntopalvelu", "id": "lp", "score": 5}
    missing_source = {"timestamp": now, "id": "legacy", "score": 4}
    valiokunta = {"timestamp": now, "source": "talousvaliokunta", "id": "HE 1/2026 vp", "score": 5}
    state_paths.score_log.write_text(
        "\n".join([_line(lausunto), _line(missing_source), _line(valiokunta)]) + "\n",
        encoding="utf-8",
    )

    main._migrate_score_log_split()

    lausunto_entries = [
        json.loads(line)
        for line in state_paths.score_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    valiokunta_entries = [
        json.loads(line)
        for line in state_paths.valiokunta_score_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert lausunto_entries == [lausunto, missing_source]
    assert valiokunta_entries == [valiokunta]
    assert state_paths.score_log_migration_marker.exists()


def test_score_log_split_migration_marker_prevents_duplicate_moves(state_paths) -> None:
    now = datetime.now(main.UTC).isoformat()
    valiokunta = {"timestamp": now, "source": "talousvaliokunta", "id": "HE 1/2026 vp", "score": 5}
    state_paths.score_log.write_text(_line(valiokunta) + "\n", encoding="utf-8")

    main._migrate_score_log_split()
    main._migrate_score_log_split()

    lines = [
        line
        for line in state_paths.valiokunta_score_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 1
