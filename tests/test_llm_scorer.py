from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Literal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from processing import llm_scorer


def _scoring_config(
    *,
    model: str = "claude-haiku-4-5",
    max_tokens: int = 300,
    timeout_seconds: float = 45.0,
    prompt_cache: bool = True,
    cache_ttl: Literal["5m", "1h"] = "5m",
) -> llm_scorer.ScoringConfig:
    return llm_scorer.ScoringConfig(
        model=model,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        prompt_cache=prompt_cache,
        cache_ttl=cache_ttl,
    )


def test_format_statements_includes_excerpt_when_present() -> None:
    text = llm_scorer._format_statements(
        [
            {"date": "2026-04-22", "title": "T1", "excerpt": "E1"},
            {"date": "2026-04-21", "title": "T2"},
        ]
    )

    assert "- 2026-04-22: T1" in text
    assert "E1" in text
    assert "- 2026-04-21: T2" in text


def test_format_statements_includes_tags_when_present() -> None:
    text = llm_scorer._format_statements(
        [{"date": "2026-04-22", "title": "T1", "tags": ["asuminen", "energia"]}]
    )

    assert "Teemat: asuminen, energia" in text


def test_score_item_ignores_jakelu_signal(monkeypatch) -> None:
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            content=[
                SimpleNamespace(text='{"score": 6, "rationale": "ok", "themes": ["asuminen"]}')
            ]
        )

    fake_client = SimpleNamespace(messages=SimpleNamespace(create=fake_create))
    monkeypatch.setattr(llm_scorer, "_get_client", lambda: fake_client)

    result = llm_scorer.score_item(
        title="Testiotsikko",
        abstract="Testikuvaus",
        source="lausuntopalvelu",
        context={"recent_statements": []},
        signals={"jakelu_kuluttajaliitto": True},
    )

    user_content_blocks = captured["messages"][0]["content"]
    item_text = user_content_blocks[1]["text"]

    assert "Lisa" not in item_text
    assert "Lisäsignaali" not in item_text
    assert "Kuluttajaliitto ry" not in item_text
    assert result["score"] == 6


def test_score_item_uses_configured_model_parameters(monkeypatch) -> None:
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(text='{"score": 6, "rationale": "ok"}')])

    fake_client = SimpleNamespace(messages=SimpleNamespace(create=fake_create))
    monkeypatch.setattr(llm_scorer, "_get_client", lambda: fake_client)
    monkeypatch.setattr(
        llm_scorer,
        "load_scoring_config",
        lambda: _scoring_config(
            model="claude-sonnet-4-6",
            max_tokens=500,
            timeout_seconds=60.0,
        ),
    )

    llm_scorer.score_item("A", "B", "src", {"recent_statements": []})

    assert captured["model"] == "claude-sonnet-4-6"
    assert captured["max_tokens"] == 500
    assert captured["timeout"] == 60.0


def test_score_item_adds_cache_controls_when_enabled(monkeypatch) -> None:
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(text='{"score": 6, "rationale": "ok"}')])

    fake_client = SimpleNamespace(messages=SimpleNamespace(create=fake_create))
    monkeypatch.setattr(llm_scorer, "_get_client", lambda: fake_client)
    monkeypatch.setattr(
        llm_scorer,
        "load_scoring_config",
        lambda: _scoring_config(cache_ttl="1h"),
    )

    llm_scorer.score_item("A", "B", "src", {"recent_statements": []})

    assert captured["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert captured["messages"][0]["content"][0]["cache_control"] == {
        "type": "ephemeral",
        "ttl": "1h",
    }
    assert "cache_control" not in captured["messages"][0]["content"][1]


def test_score_item_omits_cache_controls_when_disabled(monkeypatch) -> None:
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(text='{"score": 6, "rationale": "ok"}')])

    fake_client = SimpleNamespace(messages=SimpleNamespace(create=fake_create))
    monkeypatch.setattr(llm_scorer, "_get_client", lambda: fake_client)
    monkeypatch.setattr(
        llm_scorer,
        "load_scoring_config",
        lambda: _scoring_config(prompt_cache=False),
    )

    llm_scorer.score_item("A", "B", "src", {"recent_statements": []})

    assert "cache_control" not in captured["system"][0]
    assert "cache_control" not in captured["messages"][0]["content"][0]
    assert "cache_control" not in captured["messages"][0]["content"][1]


def test_score_item_parses_json_inside_markdown_fence(monkeypatch) -> None:
    def fake_create(**kwargs):
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    text=('```json\n{"score": 5, "rationale": "ok", "themes": ["teema"]}\n```')
                )
            ]
        )

    fake_client = SimpleNamespace(messages=SimpleNamespace(create=fake_create))
    monkeypatch.setattr(llm_scorer, "_get_client", lambda: fake_client)

    result = llm_scorer.score_item(
        title="A",
        abstract="B",
        source="lausuntopalvelu",
        context={"recent_statements": []},
    )

    assert result["score"] == 5
    assert result["themes"] == ["teema"]


def test_score_item_raises_on_empty_payload(monkeypatch) -> None:
    def fake_create(**kwargs):
        return SimpleNamespace(content=[SimpleNamespace(text="   ")])

    fake_client = SimpleNamespace(messages=SimpleNamespace(create=fake_create))
    monkeypatch.setattr(llm_scorer, "_get_client", lambda: fake_client)

    with pytest.raises(ValueError, match="non-empty text payload"):
        llm_scorer.score_item(
            title="A",
            abstract="B",
            source="lausuntopalvelu",
            context={"recent_statements": []},
        )


def test_parse_response_json_accepts_prose_wrapped_object() -> None:
    parsed = llm_scorer._parse_response_json(
        'Vastaus alla: {"score": 4, "rationale": "ok", "themes": ["teema"]} kiitos.'
    )

    assert parsed["score"] == 4


def test_parse_response_json_rejects_non_object_payload() -> None:
    with pytest.raises(ValueError, match="not valid JSON object"):
        llm_scorer._parse_response_json("[1, 2, 3]")


def test_parse_response_json_ignores_empty_fenced_block() -> None:
    parsed = llm_scorer._parse_response_json(
        '```json\n\n```\n{"score": 3, "rationale": "ok", "themes": []}'
    )

    assert parsed["score"] == 3


def test_parse_response_json_rejects_unbalanced_object() -> None:
    with pytest.raises(ValueError, match="not valid JSON object"):
        llm_scorer._parse_response_json('prefix {"score": 1, "rationale": "oops"')


def test_parse_response_json_uses_first_complete_object_when_multiple() -> None:
    parsed = llm_scorer._parse_response_json(
        '{"score": 2, "rationale": "first", "themes": []} '
        '{"score": 9, "rationale": "second", "themes": []}'
    )

    assert parsed["score"] == 2


_SAFE_TEXT = st.text(
    alphabet=st.characters(blacklist_characters="{}"),
    min_size=0,
    max_size=40,
)


@settings(max_examples=120, deadline=None)
@given(
    case=st.fixed_dictionaries(
        {
            "score": st.integers(min_value=0, max_value=10),
            "rationale": _SAFE_TEXT,
            "themes": st.lists(_SAFE_TEXT, max_size=3),
            "prefix": st.text(alphabet=st.characters(blacklist_characters="{}`"), max_size=20),
            "suffix": st.text(alphabet=st.characters(blacklist_characters="{}`"), max_size=20),
            "wrapper": st.sampled_from(["plain", "fenced", "prose"]),
        }
    )
)
def test_parse_response_json_property_fuzz_wrappers(case: dict[str, Any]) -> None:
    score = int(case["score"])
    rationale = str(case["rationale"])
    themes = [str(x) for x in case["themes"]]
    prefix = str(case["prefix"])
    suffix = str(case["suffix"])
    wrapper = str(case["wrapper"])

    payload = {"score": score, "rationale": rationale, "themes": themes}
    payload_json = json.dumps(payload, ensure_ascii=False)

    if wrapper == "plain":
        raw = payload_json
    elif wrapper == "fenced":
        raw = f"```json\n{payload_json}\n```"
    else:
        raw = f"{prefix}{payload_json}{suffix}"

    parsed = llm_scorer._parse_response_json(raw)
    assert parsed["score"] == score
    assert parsed["rationale"] == rationale
    assert parsed["themes"] == themes


@settings(max_examples=80, deadline=None)
@given(
    score=st.integers(min_value=0, max_value=10),
    rationale=_SAFE_TEXT,
    themes=st.lists(_SAFE_TEXT, max_size=3),
)
def test_parse_response_json_property_fuzz_empty_fence_then_json(
    score: int,
    rationale: str,
    themes: list[str],
) -> None:
    payload = {"score": score, "rationale": rationale, "themes": themes}
    payload_json = json.dumps(payload, ensure_ascii=False)
    raw = f"```json\n\n```\n{payload_json}"

    parsed = llm_scorer._parse_response_json(raw)
    assert parsed["score"] == score
    assert parsed["rationale"] == rationale
    assert parsed["themes"] == themes
