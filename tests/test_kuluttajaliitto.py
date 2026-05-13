from __future__ import annotations

import httpx

import clients.kuluttajaliitto as kk
from utils.text import strip_html


def test_strip_handles_html_and_none() -> None:
    assert strip_html(None) == ""
    assert strip_html("<p>Hei &amp; moi</p>") == "Hei & moi"


def test_fetch_statements_parses_wp_response() -> None:
    posts_payload = [
        {
            "id": 123,
            "date": "2026-04-20T10:30:00",
            "title": {"rendered": "<strong>Lausunto</strong>"},
            "excerpt": {"rendered": "<p>Tiivistelma &amp; huomio</p>"},
            "link": "https://example.invalid/lausunto-1",
            "tags": [3606, 9999],
        }
    ]
    tags_payload = [
        {"id": 3606, "name": "lausunto"},
        {"id": 9999, "name": "kuluttajansuoja"},
    ]

    calls: list[str] = []

    class _Transport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            base_url = str(request.url).split("?")[0]
            calls.append(base_url)
            if base_url == kk.WP_API:
                assert request.url.params["artikkelin_tyyppi"] == "8"
                assert request.url.params["per_page"] == "10"
                assert request.extensions["timeout"]["read"] == 20.0
                return httpx.Response(200, json=posts_payload)
            if base_url == kk.WP_TAGS_API:
                return httpx.Response(200, json=tags_payload)
            return httpx.Response(404)

    with httpx.Client(transport=_Transport()) as client:
        items = kk.fetch_statements(client, per_page=10)

    assert len(items) == 1
    assert items[0].id == 123
    assert items[0].date == "2026-04-20"
    assert items[0].title == "Lausunto"
    assert items[0].excerpt == "Tiivistelma & huomio"
    assert set(items[0].tags) == {"lausunto", "kuluttajansuoja"}
    assert kk.WP_TAGS_API in calls


def test_build_context_maps_statements() -> None:
    statements = [
        kk.Statement(
            id=1,
            date="2026-04-21",
            title="T1",
            excerpt="E1",
            url="https://example.invalid/1",
            tags=["lausunto", "asuminen"],
        )
    ]
    ctx = kk.build_context(statements)
    assert "last_updated" in ctx
    assert ctx["recent_statements"][0]["title"] == "T1"
    assert ctx["recent_statements"][0]["excerpt"] == "E1"
    assert ctx["recent_statements"][0]["tags"] == ["lausunto", "asuminen"]
