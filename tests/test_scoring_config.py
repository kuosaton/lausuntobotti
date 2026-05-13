from __future__ import annotations

import textwrap

import pytest

import config


def test_load_scoring_config_reads_committed_defaults() -> None:
    scoring_config = config.load_scoring_config(environ={})

    assert scoring_config == config.ScoringConfig(
        model="claude-haiku-4-5",
        max_tokens=300,
        timeout_seconds=45.0,
        prompt_cache=True,
        cache_ttl="5m",
    )


def test_load_scoring_config_env_overrides_toml(tmp_path) -> None:
    path = tmp_path / "model_config.toml"
    path.write_text(
        textwrap.dedent(
            """
            [scoring]
            model = "claude-haiku-4-5"
            max_tokens = 300
            timeout_seconds = 45.0
            prompt_cache = true
            cache_ttl = "5m"
            """
        ),
        encoding="utf-8",
    )

    scoring_config = config.load_scoring_config(
        path,
        environ={
            "CLAUDE_SCORING_MODEL": "claude-sonnet-4-6",
            "CLAUDE_SCORING_MAX_TOKENS": "500",
            "CLAUDE_SCORING_TIMEOUT_SECONDS": "60",
            "CLAUDE_SCORING_PROMPT_CACHE": "false",
            "CLAUDE_SCORING_CACHE_TTL": "1h",
        },
    )

    assert scoring_config == config.ScoringConfig(
        model="claude-sonnet-4-6",
        max_tokens=500,
        timeout_seconds=60.0,
        prompt_cache=False,
        cache_ttl="1h",
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True),
        ("false", False),
        ("1", True),
        ("0", False),
        ("yes", True),
        ("no", False),
    ],
)
def test_load_scoring_config_parses_env_booleans(
    tmp_path,
    raw: str,
    expected: bool,
) -> None:
    scoring_config = config.load_scoring_config(
        tmp_path / "missing.toml",
        environ={"CLAUDE_SCORING_PROMPT_CACHE": raw},
    )

    assert scoring_config.prompt_cache is expected


@pytest.mark.parametrize(
    ("env_name", "raw", "message"),
    [
        ("CLAUDE_SCORING_MAX_TOKENS", "nope", "max_tokens"),
        ("CLAUDE_SCORING_TIMEOUT_SECONDS", "slow", "timeout_seconds"),
        ("CLAUDE_SCORING_PROMPT_CACHE", "maybe", "prompt_cache"),
        ("CLAUDE_SCORING_CACHE_TTL", "24h", "cache_ttl"),
    ],
)
def test_load_scoring_config_rejects_invalid_env_values(
    tmp_path,
    env_name: str,
    raw: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        config.load_scoring_config(tmp_path / "missing.toml", environ={env_name: raw})


def test_load_scoring_config_rejects_invalid_toml_values(tmp_path) -> None:
    path = tmp_path / "model_config.toml"
    path.write_text(
        textwrap.dedent(
            """
            [scoring]
            model = "claude-haiku-4-5"
            max_tokens = 0
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="max_tokens"):
        config.load_scoring_config(path, environ={})
