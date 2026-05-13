from __future__ import annotations

import json
from types import SimpleNamespace

import config
import main
from clients.eduskunta import Document, Matter


def _setup_weekly_state(state_paths, monkeypatch):
    seen_documents = state_paths.seen.parent / "seen_documents.json"
    seen_documents.write_text("{}", encoding="utf-8")
    state_paths.context.write_text(
        json.dumps({"last_updated": None, "recent_statements": [{"title": "x"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "SEEN_DOCUMENTS_PATH", seen_documents)
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


def test_cmd_weekly_no_new_agendas_exits_cleanly(state_paths, monkeypatch, capsys) -> None:
    _setup_weekly_state(state_paths, monkeypatch)
    monkeypatch.setattr(main, "fetch_committee_page", lambda client, url: "<html/>")
    monkeypatch.setattr(main, "extract_documents", lambda html: [])

    def _should_not_run(*args, **kwargs):
        raise AssertionError("score_item should not run when there are no agendas")

    monkeypatch.setattr(main, "score_item", _should_not_run)

    main.cmd_weekly(dry_run=True)

    assert "No new committee agendas" in capsys.readouterr().out


def test_cmd_weekly_skips_already_seen_agendas(state_paths, monkeypatch, capsys) -> None:
    seen_documents = _setup_weekly_state(state_paths, monkeypatch)
    seen_documents.write_text(
        json.dumps({"EDK-already-seen": {"first_seen": "2026-04-20T00:00:00+00:00"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "fetch_committee_page", lambda client, url: "<html/>")
    monkeypatch.setattr(
        main, "extract_documents", lambda html: [_doc(edktunnus="EDK-already-seen")]
    )

    main.cmd_weekly(dry_run=True)

    assert "No new committee agendas" in capsys.readouterr().out


def test_cmd_weekly_aborts_on_user_no(state_paths, monkeypatch, capsys) -> None:
    seen_documents = _setup_weekly_state(state_paths, monkeypatch)
    monkeypatch.setattr(main, "fetch_committee_page", lambda client, url: "<html/>")
    monkeypatch.setattr(main, "extract_documents", lambda html: [_doc()])
    monkeypatch.setattr(main, "fetch_agenda_xml", lambda client, tunnus: "<xml/>")
    monkeypatch.setattr(main, "parse_agenda_matters", lambda xml: [_matter()])
    monkeypatch.setattr("builtins.input", lambda _: "n")

    def _should_not_score(*args, **kwargs):
        raise AssertionError("score_item should not run after abort")

    monkeypatch.setattr(main, "score_item", _should_not_score)

    main.cmd_weekly(dry_run=True)

    assert "Aborted." in capsys.readouterr().out
    assert json.loads(seen_documents.read_text(encoding="utf-8")) == {}
    assert state_paths.score_log.read_text(encoding="utf-8") == ""


def test_cmd_weekly_dry_run_scores_and_renders_digest(
    state_paths,
    monkeypatch,
    capsys,
) -> None:
    seen_documents = _setup_weekly_state(state_paths, monkeypatch)
    monkeypatch.setattr(main, "fetch_committee_page", lambda client, url: "<html/>")
    monkeypatch.setattr(main, "extract_documents", lambda html: [_doc()])
    monkeypatch.setattr(main, "fetch_agenda_xml", lambda client, tunnus: "<xml/>")
    monkeypatch.setattr(
        main,
        "parse_agenda_matters",
        lambda xml: [
            _matter(eduskuntatunnus="HE 1/2026 vp", title="Nostettava"),
            _matter(eduskuntatunnus="HE 2/2026 vp", title="Rajatapaus"),
            _matter(eduskuntatunnus="HE 3/2026 vp", title="Pudotettava"),
        ],
    )

    scores_by_title = {"Nostettava": 8, "Rajatapaus": 5, "Pudotettava": 1}
    monkeypatch.setattr(
        main,
        "score_item",
        lambda title, abstract, src, ctx: {
            "score": scores_by_title[title],
            "rationale": f"R-{title}",
            "themes": [],
        },
    )
    monkeypatch.setattr(
        main,
        "build_weekly_digest",
        lambda items, week, total_scored, total_logged, borderline_items=None: (
            f"SUBJ vko{week}",
            "<html/>",
            f"TEXT scored={total_scored} logged={total_logged} flagged={sum(len(v) for v in items.values())}",
        ),
    )

    def _should_not_send(*args, **kwargs):
        raise AssertionError("send_email should not run in dry-run")

    monkeypatch.setattr(main, "send_email", _should_not_send)

    main.cmd_weekly(dry_run=True)
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


def test_cmd_weekly_non_dry_run_sends_email(state_paths, monkeypatch) -> None:
    _setup_weekly_state(state_paths, monkeypatch)
    monkeypatch.setattr(main, "fetch_committee_page", lambda client, url: "<html/>")
    monkeypatch.setattr(main, "extract_documents", lambda html: [_doc()])
    monkeypatch.setattr(main, "fetch_agenda_xml", lambda client, tunnus: "<xml/>")
    monkeypatch.setattr(main, "parse_agenda_matters", lambda xml: [_matter()])
    monkeypatch.setattr(
        main,
        "score_item",
        lambda *args, **kwargs: {"score": 9, "rationale": "OK", "themes": []},
    )
    monkeypatch.setattr(main, "build_weekly_digest", lambda *args, **kwargs: ("S", "<h/>", "T"))

    captured: dict = {}
    monkeypatch.setattr(
        main,
        "send_email",
        lambda subject, html_body, text_body: captured.update(
            {"subject": subject, "html": html_body, "text": text_body}
        ),
    )

    main.cmd_weekly(dry_run=False)

    assert captured == {"subject": "S", "html": "<h/>", "text": "T"}


def test_cmd_weekly_declined_send_does_not_mark_notified(state_paths, monkeypatch) -> None:
    seen_documents = _setup_weekly_state(state_paths, monkeypatch)
    inputs = iter(["y", "n"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr(main, "fetch_committee_page", lambda client, url: "<html/>")
    monkeypatch.setattr(main, "extract_documents", lambda html: [_doc()])
    monkeypatch.setattr(main, "fetch_agenda_xml", lambda client, tunnus: "<xml/>")
    monkeypatch.setattr(main, "parse_agenda_matters", lambda xml: [_matter()])
    monkeypatch.setattr(
        main,
        "score_item",
        lambda *args, **kwargs: {"score": 9, "rationale": "OK", "themes": []},
    )
    monkeypatch.setattr(main, "build_weekly_digest", lambda *args, **kwargs: ("S", "<h/>", "T"))

    sent = {"called": False}
    monkeypatch.setattr(
        main,
        "send_email",
        lambda *args, **kwargs: sent.__setitem__("called", True),
    )

    main.cmd_weekly(dry_run=False)

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


def test_cmd_weekly_handles_agenda_fetch_error(state_paths, monkeypatch, capsys) -> None:
    _setup_weekly_state(state_paths, monkeypatch)
    monkeypatch.setattr(main, "fetch_committee_page", lambda client, url: "<html/>")
    monkeypatch.setattr(main, "extract_documents", lambda html: [_doc()])

    def _raise(client, tunnus):
        raise RuntimeError("vaski down")

    monkeypatch.setattr(main, "fetch_agenda_xml", _raise)

    main.cmd_weekly(dry_run=True)

    assert "No matters scheduled" in capsys.readouterr().out


def test_cmd_weekly_skips_non_agenda_documents(state_paths, monkeypatch, capsys) -> None:
    _setup_weekly_state(state_paths, monkeypatch)
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
    monkeypatch.setattr(main, "fetch_committee_page", lambda client, url: "<html/>")
    monkeypatch.setattr(main, "extract_documents", lambda html: docs)

    main.cmd_weekly(dry_run=True)

    assert "No new committee agendas" in capsys.readouterr().out
