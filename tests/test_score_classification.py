from __future__ import annotations

import config
from processing.score_classification import classify_score


def test_classify_score_uses_configured_thresholds(monkeypatch) -> None:
    monkeypatch.setattr(config, "FLAG_THRESHOLD", 6)
    monkeypatch.setattr(config, "LOG_THRESHOLD", 4)

    assert classify_score(0) == "drop"
    assert classify_score(3) == "drop"
    assert classify_score(4) == "log"
    assert classify_score(6) == "flag"
    assert classify_score(10) == "flag"
