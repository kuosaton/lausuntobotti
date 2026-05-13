from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

# Scoring thresholds
NOTIFY_THRESHOLD = 6  # score >= 6 → email
LOG_THRESHOLD = 4  # score 4-5 → log only; score 0-3 → drop

# How many proposals to fetch per lausuntopyyntö run (sorted newest-first).
# High enough to cover the full backlog on first run; deduplication handles the rest.
LAUSUNTOPALVELU_FETCH_TOP = 200
CONTEXT_MAX_AGE_DAYS = 7

# Paths
ROOT = Path(__file__).parent
STATE_DIR = ROOT / "state"
CONTEXT_DIR = ROOT / "context"
STATE_DIR.mkdir(exist_ok=True)
CONTEXT_DIR.mkdir(exist_ok=True)
SEEN_PROPOSALS_PATH = STATE_DIR / "seen_proposals.json"
SEEN_DOCUMENTS_PATH = STATE_DIR / "seen_documents.json"
LAUSUNTOPALVELU_SCORE_LOG_PATH = STATE_DIR / "score_log.jsonl"
VALIOKUNTA_SCORE_LOG_PATH = STATE_DIR / "valiokunta_score_log.jsonl"
SCORE_LOG_PATH = LAUSUNTOPALVELU_SCORE_LOG_PATH
SCORE_LOG_SPLIT_MIGRATION_MARKER = STATE_DIR / ".score_log_split_migrated"
FLAGGED_PATH = STATE_DIR / "nostetut.json"
CONTEXT_PATH = CONTEXT_DIR / "kuluttajaliitto.json"
SCORING_CONFIG_PATH = ROOT / "model_config.toml"


@dataclass(frozen=True)
class ScoringConfig:
    model: str
    max_tokens: int
    timeout_seconds: float
    prompt_cache: bool
    cache_ttl: Literal["5m", "1h"]


_DEFAULT_SCORING_CONFIG = ScoringConfig(
    model="claude-haiku-4-5",
    max_tokens=300,
    timeout_seconds=45.0,
    prompt_cache=True,
    cache_ttl="5m",
)
_CACHE_TTLS = {"5m", "1h"}
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _scoring_table(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    scoring = data.get("scoring", {})
    if not isinstance(scoring, dict):
        raise ValueError("[scoring] in model_config.toml must be a table")
    return cast(dict[str, object], scoring)


def _string_setting(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _int_setting(name: str, value: object) -> int:
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc
    elif isinstance(value, int) and not isinstance(value, bool):
        parsed = value
    else:
        raise ValueError(f"{name} must be an integer")
    if parsed <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return parsed


def _float_setting(name: str, value: object) -> float:
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError as exc:
            raise ValueError(f"{name} must be a number") from exc
    elif isinstance(value, int | float) and not isinstance(value, bool):
        parsed = float(value)
    else:
        raise ValueError(f"{name} must be a number")
    if parsed <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return parsed


def _bool_setting(name: str, value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in _TRUE_VALUES:
            return True
        if normalized in _FALSE_VALUES:
            return False
    raise ValueError(f"{name} must be a boolean")


def _cache_ttl_setting(name: str, value: object) -> Literal["5m", "1h"]:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be one of: 5m, 1h")
    normalized = value.strip()
    if normalized not in _CACHE_TTLS:
        raise ValueError(f"{name} must be one of: 5m, 1h")
    return cast(Literal["5m", "1h"], normalized)


def load_scoring_config(
    path: Path = SCORING_CONFIG_PATH,
    environ: Mapping[str, str] | None = None,
) -> ScoringConfig:
    env = os.environ if environ is None else environ
    values: dict[str, object] = {
        "model": _DEFAULT_SCORING_CONFIG.model,
        "max_tokens": _DEFAULT_SCORING_CONFIG.max_tokens,
        "timeout_seconds": _DEFAULT_SCORING_CONFIG.timeout_seconds,
        "prompt_cache": _DEFAULT_SCORING_CONFIG.prompt_cache,
        "cache_ttl": _DEFAULT_SCORING_CONFIG.cache_ttl,
    }
    values.update(_scoring_table(path))

    env_overrides = {
        "CLAUDE_SCORING_MODEL": "model",
        "CLAUDE_SCORING_MAX_TOKENS": "max_tokens",
        "CLAUDE_SCORING_TIMEOUT_SECONDS": "timeout_seconds",
        "CLAUDE_SCORING_PROMPT_CACHE": "prompt_cache",
        "CLAUDE_SCORING_CACHE_TTL": "cache_ttl",
    }
    for env_name, setting_name in env_overrides.items():
        if env_name in env:
            values[setting_name] = env[env_name]

    return ScoringConfig(
        model=_string_setting("scoring.model", values["model"]),
        max_tokens=_int_setting("scoring.max_tokens", values["max_tokens"]),
        timeout_seconds=_float_setting("scoring.timeout_seconds", values["timeout_seconds"]),
        prompt_cache=_bool_setting("scoring.prompt_cache", values["prompt_cache"]),
        cache_ttl=_cache_ttl_setting("scoring.cache_ttl", values["cache_ttl"]),
    )


# Committee pages – these are the main pages that embed schedule + agenda data
COMMITTEE_URLS = {
    "talousvaliokunta": "https://www.eduskunta.fi/kansanedustajat-ja-toimielimet/valiokunnat/talousvaliokunta",
    "maa_ja_metsatalousvaliokunta": "https://www.eduskunta.fi/kansanedustajat-ja-toimielimet/valiokunnat/maa-ja-metsatalousvaliokunta",
    "ymparistovaliokunta": "https://www.eduskunta.fi/kansanedustajat-ja-toimielimet/valiokunnat/ymparistovaliokunta",
}

COMMITTEE_DISPLAY_NAMES = {
    "talousvaliokunta": "Talousvaliokunta",
    "maa_ja_metsatalousvaliokunta": "Maa- ja metsätalousvaliokunta",
    "ymparistovaliokunta": "Ympäristövaliokunta",
}
