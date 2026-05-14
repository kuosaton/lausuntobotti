from __future__ import annotations

from typing import Literal

import config

ScoreBand = Literal["flag", "log", "drop"]


def classify_score(score: int) -> ScoreBand:
    if score >= config.FLAG_THRESHOLD:
        return "flag"
    if score >= config.LOG_THRESHOLD:
        return "log"
    return "drop"
