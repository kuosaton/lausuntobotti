"""End-to-end tests for cmd_weekly.

These tests exercise the committee pipeline — fetch agenda page → parse agenda →
fetch XML → parse matters → score → log → render → send — with only external
boundaries stubbed. The real Eduskunta parsers, valiokunta digest builder, state
helpers, and Resend email payload construction run so field handoffs stay covered.
"""

from __future__ import annotations

import json
from datetime import date

import resend

import config
import main
import workflows.valiokunta as valiokunta_workflow


COMMITTEE_HTML = """
<script>
{edktunnus:"EDK-2026-AK-40",eduskuntatunnus:"TaVE 40/2026 vp",asiakirjatyyppinimi:"Esityslista",asiakirjatyyppikoodi:"TaVE",nimeketeksti:"Tiistai 28.4.2026 klo 12.00",valiokuntanimi:null,laadintapvm:"2026-04-28",viimeisinJulkaisuajankohta:"2026-04-24T11:21:24.988+00:00"}
{edktunnus:"EDK-2026-AK-39",eduskuntatunnus:"TaVP 39/2026 vp",asiakirjatyyppinimi:"Pöytäkirja",asiakirjatyyppikoodi:"TaVP",nimeketeksti:"Pöytäkirja",valiokuntanimi:null,laadintapvm:"2026-04-21",viimeisinJulkaisuajankohta:"2026-04-22T13:09:22.227+00:00"}
</script>
"""


AGENDA_XML = """
<root xmlns:vsk="http://www.eduskunta.fi/skeemat/vaskikooste/2011/01/04"
      xmlns:met1="http://www.vn.fi/skeemat/metatietoelementit/2010/04/27">
  <vsk:Asiakohta>
    <vsk:KohtaNimeke>
      <met1:NimekeTeksti>Hallituksen esitys kuluttajansuojalain muuttamisesta</met1:NimekeTeksti>
    </vsk:KohtaNimeke>
    <vsk:KohtaAsia>
      <met1:AsiakirjatyyppiNimi>Hallituksen esitys</met1:AsiakirjatyyppiNimi>
      <met1:EduskuntaTunnus>HE 37/2026 vp</met1:EduskuntaTunnus>
    </vsk:KohtaAsia>
  </vsk:Asiakohta>
  <vsk:Asiakohta>
    <vsk:KohtaNimeke>
      <met1:NimekeTeksti>Valtioneuvoston kirjelmä valtiontuista</met1:NimekeTeksti>
    </vsk:KohtaNimeke>
    <vsk:KohtaAsia>
      <met1:AsiakirjatyyppiNimi>Valtioneuvoston U-kirjelmä</met1:AsiakirjatyyppiNimi>
      <met1:EduskuntaTunnus>U 27/2026 vp</met1:EduskuntaTunnus>
    </vsk:KohtaAsia>
  </vsk:Asiakohta>
  <vsk:Asiakohta>
    <vsk:KohtaNimeke>
      <met1:NimekeTeksti>Asia ilman kuluttajakytkentää</met1:NimekeTeksti>
    </vsk:KohtaNimeke>
    <vsk:KohtaAsia>
      <met1:AsiakirjatyyppiNimi>Hallituksen esitys</met1:AsiakirjatyyppiNimi>
      <met1:EduskuntaTunnus>HE 99/2026 vp</met1:EduskuntaTunnus>
    </vsk:KohtaAsia>
  </vsk:Asiakohta>
</root>
"""


def test_cmd_weekly_full_pipeline_renders_real_digest(state_paths, monkeypatch) -> None:
    seen_documents = state_paths.seen.parent / "seen_documents.json"
    seen_documents.write_text("{}", encoding="utf-8")
    state_paths.context.write_text(
        json.dumps({"last_updated": None, "recent_statements": [{"title": "Kuluttajansuoja"}]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "SEEN_DOCUMENTS_PATH", seen_documents)
    monkeypatch.setenv("SENDER_EMAIL", "botti@example.com")
    monkeypatch.setenv("RECIPIENT_EMAIL", "vastaanottaja@example.com")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")

    monkeypatch.setattr(
        valiokunta_workflow, "fetch_committee_page", lambda client, url: COMMITTEE_HTML
    )
    monkeypatch.setattr(valiokunta_workflow, "fetch_agenda_xml", lambda client, tunnus: AGENDA_XML)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")

    scores_by_title = {
        "Hallituksen esitys kuluttajansuojalain muuttamisesta": {
            "score": 9,
            "rationale": "Suora kuluttajansuojaosuma.",
            "themes": ["kuluttajansuoja", "sopimusehdot"],
        },
        "Valtioneuvoston kirjelmä valtiontuista": {
            "score": 5,
            "rationale": "Välillinen markkinavaikutus.",
            "themes": ["kilpailu"],
        },
        "Asia ilman kuluttajakytkentää": {
            "score": 1,
            "rationale": "Ei kuluttajakytkentää.",
            "themes": [],
        },
    }
    monkeypatch.setattr(
        valiokunta_workflow,
        "score_item",
        lambda title, abstract, source, ctx: dict(scores_by_title[title]),
    )

    captured_email: dict = {}

    def fake_send(params):
        captured_email.update(params)
        return {"id": "weekly-fake-id"}

    monkeypatch.setattr(resend.Emails, "send", staticmethod(fake_send))

    main.cmd_weekly(dry_run=False)

    assert captured_email["from"] == "botti@example.com"
    assert captured_email["to"] == ["vastaanottaja@example.com"]

    week_number = date.today().isocalendar().week
    assert captured_email["subject"] == f"Lausuntobotin viikkokatsaus, vko {week_number}"

    text = captured_email["text"]
    html = captured_email["html"]

    assert "TALOUSVALIOKUNTA" in text
    assert "Hallituksen esitys kuluttajansuojalain muuttamisesta" in text
    assert "HE 37/2026 vp" in text
    assert "Relevanssi: 9/10" in text
    assert "Suora kuluttajansuojaosuma." in text
    assert "Hallituksen esitys kuluttajansuojalain muuttamisesta" in html
    assert "kuluttajansuoja, sopimusehdot" in html

    assert "Rajatapauksia" in text
    assert "Valtioneuvoston kirjelmä valtiontuista" in text
    assert "[5/10] Valtioneuvoston kirjelmä valtiontuista" in text
    assert "Välillinen markkinavaikutus." in text
    assert "Asia ilman kuluttajakytkentää" not in text
    assert "Arvioitu yhteensä: 3 asiaa" in text
    assert "Nostettu: 1" in text
    assert "Rajatapauksia: 1" in text

    seen_docs = json.loads(seen_documents.read_text(encoding="utf-8"))
    matter_scores = seen_docs["EDK-2026-AK-40"]["matter_scores"]
    assert matter_scores == {
        "HE 37/2026 vp": {"score": 9, "notified": True},
        "U 27/2026 vp": {"score": 5, "notified": False},
        "HE 99/2026 vp": {"score": 1, "notified": False},
    }

    log_entries = [
        json.loads(line)
        for line in state_paths.valiokunta_score_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert {entry["id"] for entry in log_entries} == {
        "HE 37/2026 vp",
        "U 27/2026 vp",
        "HE 99/2026 vp",
    }
    assert all(entry["source"] == "talousvaliokunta" for entry in log_entries)
