from __future__ import annotations

import json
from types import SimpleNamespace

import config
import main
import workflows.valiokunta as valiokunta_workflow
from clients.eduskunta import Document, Matter


def _setup_valiokunta_state(
    state_paths,
    monkeypatch,
    valiokunta_committees: tuple[str, ...] | None = ("talousvaliokunta",),
):
    seen_documents = state_paths.seen.parent / "seen_documents.json"
    seen_documents.write_text("{}", encoding="utf-8")
    state_paths.context.write_text(
        json.dumps({"last_updated": None, "recent_statements": [{"title": "x"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "SEEN_DOCUMENTS_PATH", seen_documents)
    if valiokunta_committees is not None:
        monkeypatch.setattr(valiokunta_workflow, "_VALIOKUNTA_COMMITTEES", valiokunta_committees)
    return seen_documents


def _doc(
    edktunnus: str = "EDK-1",
    eduskuntatunnus: str | None = "TaVE 40/2026 vp",
    tyyppikoodi: str = "TaVE",
) -> Document:
    return Document(
        edktunnus=edktunnus,
        eduskuntatunnus=eduskuntatunnus,
        tyyppikoodi=tyyppikoodi,
        nimeke="Tiistai 28.4.2026 klo 12.00",
        laadintapvm="2026-04-28",
        julkaistu="2026-04-25T08:00:00.000+00:00",
    )


def _matter(eduskuntatunnus: str = "HE 1/2026 vp", title: str = "Esimerkkiasia") -> Matter:
    return Matter(eduskuntatunnus=eduskuntatunnus, title=title, type="Hallituksen esitys")


def test_cmd_valiokunta_no_new_agendas_exits_cleanly(state_paths, monkeypatch, capsys) -> None:
    _setup_valiokunta_state(state_paths, monkeypatch)
    monkeypatch.setattr(valiokunta_workflow, "fetch_committee_page", lambda client, url: "<html/>")
    monkeypatch.setattr(valiokunta_workflow, "extract_documents", lambda html: [])

    def _should_not_run(*args, **kwargs):
        raise AssertionError("score_item should not run when there are no agendas")

    monkeypatch.setattr(valiokunta_workflow, "score_item", _should_not_run)

    main.cmd_valiokunta(dry_run=True)

    assert "No new committee agendas" in capsys.readouterr().out


def test_cmd_valiokunta_fetches_all_priority_committees_by_default(
    state_paths,
    monkeypatch,
    capsys,
) -> None:
    _setup_valiokunta_state(state_paths, monkeypatch, valiokunta_committees=None)
    fetched_urls = []

    def _fetch_page(client, url):
        fetched_urls.append(url)
        return "<html/>"

    monkeypatch.setattr(valiokunta_workflow, "fetch_committee_page", _fetch_page)
    monkeypatch.setattr(valiokunta_workflow, "extract_documents", lambda html: [])

    main.cmd_valiokunta(dry_run=True)

    assert fetched_urls == [config.COMMITTEE_URLS[key] for key in config.COMMITTEE_URLS]
    out = capsys.readouterr().out
    assert "Fetching Talousvaliokunta" in out
    assert "Fetching Maa- ja metsätalousvaliokunta" in out
    assert "Fetching Ympäristövaliokunta" in out


def test_cmd_valiokunta_scores_and_logs_multiple_committees_by_default(
    state_paths,
    monkeypatch,
) -> None:
    seen_documents = _setup_valiokunta_state(state_paths, monkeypatch, valiokunta_committees=None)
    doc_by_url = {
        config.COMMITTEE_URLS["talousvaliokunta"]: _doc(
            edktunnus="EDK-tav",
            eduskuntatunnus="TaVE 1/2026 vp",
            tyyppikoodi="TaVE",
        ),
        config.COMMITTEE_URLS["maa_ja_metsatalousvaliokunta"]: _doc(
            edktunnus="EDK-mmv",
            eduskuntatunnus="MmVE 1/2026 vp",
            tyyppikoodi="MmVE",
        ),
        config.COMMITTEE_URLS["ymparistovaliokunta"]: _doc(
            edktunnus="EDK-ymv",
            eduskuntatunnus="YmVE 1/2026 vp",
            tyyppikoodi="YmVE",
        ),
    }
    matter_by_agenda = {
        "TaVE 1/2026 vp": _matter(eduskuntatunnus="HE 1/2026 vp", title="TaV asia"),
        "MmVE 1/2026 vp": _matter(eduskuntatunnus="HE 2/2026 vp", title="MmV asia"),
        "YmVE 1/2026 vp": _matter(eduskuntatunnus="HE 3/2026 vp", title="YmV asia"),
    }
    scores_by_title = {"TaV asia": 1, "MmV asia": 8, "YmV asia": 5}
    captured_digest: dict = {}

    monkeypatch.setattr(valiokunta_workflow, "fetch_committee_page", lambda client, url: url)
    monkeypatch.setattr(
        valiokunta_workflow,
        "extract_documents",
        lambda html: [doc_by_url[html]],
    )
    monkeypatch.setattr(valiokunta_workflow, "fetch_agenda_xml", lambda client, tunnus: tunnus)
    monkeypatch.setattr(
        valiokunta_workflow,
        "parse_agenda_matters",
        lambda xml: [matter_by_agenda[xml]],
    )
    monkeypatch.setattr(
        valiokunta_workflow,
        "score_item",
        lambda title, abstract, src, ctx: {
            "score": scores_by_title[title],
            "rationale": f"R-{title}",
            "themes": [],
        },
    )

    def _build_digest(items, week, total_scored, total_logged, borderline_items=None):
        captured_digest.update(
            {
                "items": items,
                "total_scored": total_scored,
                "total_logged": total_logged,
                "borderline_items": borderline_items,
            }
        )
        return ("S", "<h/>", "T")

    monkeypatch.setattr(valiokunta_workflow, "build_valiokunta_digest", _build_digest)
    monkeypatch.setattr("builtins.input", lambda _: "y")

    main.cmd_valiokunta(dry_run=True)

    assert captured_digest["total_scored"] == 3
    assert captured_digest["total_logged"] == 1
    assert captured_digest["items"]["maa_ja_metsatalousvaliokunta"][0]["title"] == "MmV asia"
    assert (
        captured_digest["items"]["maa_ja_metsatalousvaliokunta"][0]["url"]
        == "https://www.eduskunta.fi/valtiopaivaasiat/HE+2/2026"
    )
    assert captured_digest["borderline_items"]["ymparistovaliokunta"][0]["title"] == "YmV asia"
    assert (
        captured_digest["borderline_items"]["ymparistovaliokunta"][0]["url"]
        == "https://www.eduskunta.fi/valtiopaivaasiat/HE+3/2026"
    )

    seen_docs = json.loads(seen_documents.read_text(encoding="utf-8"))
    assert set(seen_docs) == {"EDK-tav", "EDK-mmv", "EDK-ymv"}

    log_entries = [
        json.loads(line)
        for line in state_paths.valiokunta_score_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert {entry["source"] for entry in log_entries} == {
        "talousvaliokunta",
        "maa_ja_metsatalousvaliokunta",
        "ymparistovaliokunta",
    }
    assert {entry["url"] for entry in log_entries} == {
        "https://www.eduskunta.fi/valtiopaivaasiat/HE+1/2026",
        "https://www.eduskunta.fi/valtiopaivaasiat/HE+2/2026",
        "https://www.eduskunta.fi/valtiopaivaasiat/HE+3/2026",
    }


def test_cmd_valiokunta_continues_when_one_committee_page_fails(
    state_paths,
    monkeypatch,
    capsys,
) -> None:
    seen_documents = _setup_valiokunta_state(state_paths, monkeypatch, valiokunta_committees=None)
    doc_by_url = {
        config.COMMITTEE_URLS["talousvaliokunta"]: _doc(
            edktunnus="EDK-tav",
            eduskuntatunnus="TaVE 1/2026 vp",
            tyyppikoodi="TaVE",
        ),
        config.COMMITTEE_URLS["ymparistovaliokunta"]: _doc(
            edktunnus="EDK-ymv",
            eduskuntatunnus="YmVE 1/2026 vp",
            tyyppikoodi="YmVE",
        ),
    }
    matter_by_agenda = {
        "TaVE 1/2026 vp": _matter(eduskuntatunnus="HE 1/2026 vp", title="TaV asia"),
        "YmVE 1/2026 vp": _matter(eduskuntatunnus="HE 3/2026 vp", title="YmV asia"),
    }

    def _fetch_page(client, url):
        if url == config.COMMITTEE_URLS["maa_ja_metsatalousvaliokunta"]:
            raise RuntimeError("committee page down")
        return url

    monkeypatch.setattr(valiokunta_workflow, "fetch_committee_page", _fetch_page)
    monkeypatch.setattr(
        valiokunta_workflow,
        "extract_documents",
        lambda html: [doc_by_url[html]],
    )
    monkeypatch.setattr(valiokunta_workflow, "fetch_agenda_xml", lambda client, tunnus: tunnus)
    monkeypatch.setattr(
        valiokunta_workflow,
        "parse_agenda_matters",
        lambda xml: [matter_by_agenda[xml]],
    )
    monkeypatch.setattr(
        valiokunta_workflow,
        "score_item",
        lambda *args, **kwargs: {"score": 8, "rationale": "OK", "themes": []},
    )
    monkeypatch.setattr(
        valiokunta_workflow, "build_valiokunta_digest", lambda *args, **kwargs: ("S", "<h/>", "T")
    )
    monkeypatch.setattr("builtins.input", lambda _: "y")

    main.cmd_valiokunta(dry_run=True)

    captured = capsys.readouterr()
    assert (
        "could not fetch/parse Maa- ja metsätalousvaliokunta: committee page down" in captured.err
    )

    seen_docs = json.loads(seen_documents.read_text(encoding="utf-8"))
    assert set(seen_docs) == {"EDK-tav", "EDK-ymv"}

    log_entries = [
        json.loads(line)
        for line in state_paths.valiokunta_score_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert {entry["source"] for entry in log_entries} == {
        "talousvaliokunta",
        "ymparistovaliokunta",
    }


def test_cmd_valiokunta_skips_already_seen_agendas(state_paths, monkeypatch, capsys) -> None:
    seen_documents = _setup_valiokunta_state(state_paths, monkeypatch)
    seen_documents.write_text(
        json.dumps({"EDK-already-seen": {"first_seen": "2026-04-20T00:00:00+00:00"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(valiokunta_workflow, "fetch_committee_page", lambda client, url: "<html/>")
    monkeypatch.setattr(
        valiokunta_workflow,
        "extract_documents",
        lambda html: [_doc(edktunnus="EDK-already-seen")],
    )

    main.cmd_valiokunta(dry_run=True)

    assert "No new committee agendas" in capsys.readouterr().out


def test_cmd_valiokunta_aborts_on_user_no(state_paths, monkeypatch, capsys) -> None:
    seen_documents = _setup_valiokunta_state(state_paths, monkeypatch)
    monkeypatch.setattr(valiokunta_workflow, "fetch_committee_page", lambda client, url: "<html/>")
    monkeypatch.setattr(valiokunta_workflow, "extract_documents", lambda html: [_doc()])
    monkeypatch.setattr(valiokunta_workflow, "fetch_agenda_xml", lambda client, tunnus: "<xml/>")
    monkeypatch.setattr(valiokunta_workflow, "parse_agenda_matters", lambda xml: [_matter()])
    monkeypatch.setattr("builtins.input", lambda _: "n")

    def _should_not_score(*args, **kwargs):
        raise AssertionError("score_item should not run after abort")

    monkeypatch.setattr(valiokunta_workflow, "score_item", _should_not_score)

    main.cmd_valiokunta(dry_run=True)

    assert "Aborted." in capsys.readouterr().out
    assert json.loads(seen_documents.read_text(encoding="utf-8")) == {}
    assert state_paths.score_log.read_text(encoding="utf-8") == ""


def test_cmd_valiokunta_dry_run_scores_and_renders_digest(
    state_paths,
    monkeypatch,
    capsys,
) -> None:
    seen_documents = _setup_valiokunta_state(state_paths, monkeypatch)
    monkeypatch.setattr(valiokunta_workflow, "fetch_committee_page", lambda client, url: "<html/>")
    monkeypatch.setattr(valiokunta_workflow, "extract_documents", lambda html: [_doc()])
    monkeypatch.setattr(valiokunta_workflow, "fetch_agenda_xml", lambda client, tunnus: "<xml/>")
    monkeypatch.setattr(
        valiokunta_workflow,
        "parse_agenda_matters",
        lambda xml: [
            _matter(eduskuntatunnus="HE 1/2026 vp", title="Nostettava"),
            _matter(eduskuntatunnus="HE 2/2026 vp", title="Rajatapaus"),
            _matter(eduskuntatunnus="HE 3/2026 vp", title="Pudotettava"),
        ],
    )

    scores_by_title = {"Nostettava": 8, "Rajatapaus": 5, "Pudotettava": 1}
    monkeypatch.setattr(
        valiokunta_workflow,
        "score_item",
        lambda title, abstract, src, ctx: {
            "score": scores_by_title[title],
            "rationale": f"R-{title}",
            "themes": [],
        },
    )
    monkeypatch.setattr(
        valiokunta_workflow,
        "build_valiokunta_digest",
        lambda items, week, total_scored, total_logged, borderline_items=None: (
            f"SUBJ vko{week}",
            "<html/>",
            f"TEXT scored={total_scored} logged={total_logged} flagged={sum(len(v) for v in items.values())}",
        ),
    )

    def _should_not_send(*args, **kwargs):
        raise AssertionError("send_email should not run in dry-run")

    monkeypatch.setattr(valiokunta_workflow, "send_email", _should_not_send)

    main.cmd_valiokunta(dry_run=True)
    out = capsys.readouterr().out

    assert "[FLAG 8/10] HE 1/2026 vp: Nostettava" in out
    assert "[LOG 5/10] HE 2/2026 vp: Rajatapaus" in out
    assert "[DROP 1/10] HE 3/2026 vp: Pudotettava" in out
    assert "DRY RUN" in out
    assert "TEXT scored=3 logged=1 flagged=1" in out

    seen_docs = json.loads(seen_documents.read_text(encoding="utf-8"))
    assert seen_docs["EDK-1"]["matter_scores"]["HE 1/2026 vp"] == {
        "score": 8,
        "notified": False,
    }

    log_lines = [
        json.loads(line)
        for line in state_paths.valiokunta_score_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert {entry["id"] for entry in log_lines} == {
        "HE 1/2026 vp",
        "HE 2/2026 vp",
        "HE 3/2026 vp",
    }
    assert all(entry["source"] == "talousvaliokunta" for entry in log_lines)
    assert all(
        entry["url"].startswith("https://www.eduskunta.fi/valtiopaivaasiat/HE+")
        for entry in log_lines
    )


def test_cmd_valiokunta_non_dry_run_sends_email(state_paths, monkeypatch) -> None:
    _setup_valiokunta_state(state_paths, monkeypatch)
    monkeypatch.setattr(valiokunta_workflow, "fetch_committee_page", lambda client, url: "<html/>")
    monkeypatch.setattr(valiokunta_workflow, "extract_documents", lambda html: [_doc()])
    monkeypatch.setattr(valiokunta_workflow, "fetch_agenda_xml", lambda client, tunnus: "<xml/>")
    monkeypatch.setattr(valiokunta_workflow, "parse_agenda_matters", lambda xml: [_matter()])
    monkeypatch.setattr(
        valiokunta_workflow,
        "score_item",
        lambda *args, **kwargs: {"score": 9, "rationale": "OK", "themes": []},
    )
    monkeypatch.setattr(
        valiokunta_workflow, "build_valiokunta_digest", lambda *args, **kwargs: ("S", "<h/>", "T")
    )

    captured: dict = {}
    monkeypatch.setattr(
        valiokunta_workflow,
        "send_email",
        lambda subject, html_body, text_body: captured.update(
            {"subject": subject, "html": html_body, "text": text_body}
        ),
    )

    main.cmd_valiokunta(dry_run=False)

    assert captured == {"subject": "S", "html": "<h/>", "text": "T"}


def test_cmd_valiokunta_send_failure_does_not_mark_notified(
    state_paths, monkeypatch, capsys
) -> None:
    seen_documents = _setup_valiokunta_state(state_paths, monkeypatch)
    monkeypatch.setattr(valiokunta_workflow, "fetch_committee_page", lambda client, url: "<html/>")
    monkeypatch.setattr(valiokunta_workflow, "extract_documents", lambda html: [_doc()])
    monkeypatch.setattr(valiokunta_workflow, "fetch_agenda_xml", lambda client, tunnus: "<xml/>")
    monkeypatch.setattr(valiokunta_workflow, "parse_agenda_matters", lambda xml: [_matter()])
    monkeypatch.setattr(
        valiokunta_workflow,
        "score_item",
        lambda *args, **kwargs: {"score": 9, "rationale": "OK", "themes": []},
    )
    monkeypatch.setattr(
        valiokunta_workflow, "build_valiokunta_digest", lambda *args, **kwargs: ("S", "<h/>", "T")
    )

    def _raise_send(*args, **kwargs):
        raise RuntimeError("resend down")

    monkeypatch.setattr(valiokunta_workflow, "send_email", _raise_send)

    main.cmd_valiokunta(dry_run=False)

    captured = capsys.readouterr()
    assert "ERROR: email delivery failed: resend down" in captured.err
    assert "Valiokunta digest sent" not in captured.out

    seen_docs = json.loads(seen_documents.read_text(encoding="utf-8"))
    assert seen_docs["EDK-1"]["matter_scores"]["HE 1/2026 vp"] == {
        "score": 9,
        "notified": False,
    }
    log_entry = json.loads(
        state_paths.valiokunta_score_log.read_text(encoding="utf-8").splitlines()[0]
    )
    assert log_entry["notified"] is False


def test_cmd_valiokunta_declined_send_does_not_mark_notified(state_paths, monkeypatch) -> None:
    seen_documents = _setup_valiokunta_state(state_paths, monkeypatch)
    inputs = iter(["y", "n"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr(valiokunta_workflow, "fetch_committee_page", lambda client, url: "<html/>")
    monkeypatch.setattr(valiokunta_workflow, "extract_documents", lambda html: [_doc()])
    monkeypatch.setattr(valiokunta_workflow, "fetch_agenda_xml", lambda client, tunnus: "<xml/>")
    monkeypatch.setattr(valiokunta_workflow, "parse_agenda_matters", lambda xml: [_matter()])
    monkeypatch.setattr(
        valiokunta_workflow,
        "score_item",
        lambda *args, **kwargs: {"score": 9, "rationale": "OK", "themes": []},
    )
    monkeypatch.setattr(
        valiokunta_workflow, "build_valiokunta_digest", lambda *args, **kwargs: ("S", "<h/>", "T")
    )

    sent = {"called": False}
    monkeypatch.setattr(
        valiokunta_workflow,
        "send_email",
        lambda *args, **kwargs: sent.__setitem__("called", True),
    )

    main.cmd_valiokunta(dry_run=False)

    seen_docs = json.loads(seen_documents.read_text(encoding="utf-8"))
    assert seen_docs["EDK-1"]["matter_scores"]["HE 1/2026 vp"] == {
        "score": 9,
        "notified": False,
    }
    log_entry = json.loads(
        state_paths.valiokunta_score_log.read_text(encoding="utf-8").splitlines()[0]
    )
    assert log_entry["notified"] is False
    assert sent["called"] is False


def test_cmd_valiokunta_handles_agenda_fetch_error(state_paths, monkeypatch, capsys) -> None:
    _setup_valiokunta_state(state_paths, monkeypatch)
    monkeypatch.setattr(valiokunta_workflow, "fetch_committee_page", lambda client, url: "<html/>")
    monkeypatch.setattr(valiokunta_workflow, "extract_documents", lambda html: [_doc()])

    def _raise(client, tunnus):
        raise RuntimeError("vaski down")

    monkeypatch.setattr(valiokunta_workflow, "fetch_agenda_xml", _raise)

    main.cmd_valiokunta(dry_run=True)

    assert "No matters scheduled" in capsys.readouterr().out


def test_cmd_valiokunta_skips_non_agenda_documents(state_paths, monkeypatch, capsys) -> None:
    _setup_valiokunta_state(state_paths, monkeypatch)
    docs = [
        _doc(edktunnus="EDK-pp", eduskuntatunnus="TaVP 1/2026 vp", tyyppikoodi="TaVP"),
        SimpleNamespace(
            edktunnus="EDK-vs",
            eduskuntatunnus=None,
            tyyppikoodi="VS",
            nimeke="Viikkosuunnitelma",
            laadintapvm="2026-04-25",
            julkaistu="2026-04-25T08:00:00+00:00",
        ),
    ]
    monkeypatch.setattr(valiokunta_workflow, "fetch_committee_page", lambda client, url: "<html/>")
    monkeypatch.setattr(valiokunta_workflow, "extract_documents", lambda html: docs)

    main.cmd_valiokunta(dry_run=True)

    assert "No new committee agendas" in capsys.readouterr().out
