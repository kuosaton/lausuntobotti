from __future__ import annotations

import httpx
import pytest

from clients.eduskunta import (
    HEADERS,
    VASKI_URL,
    build_matter_url,
    extract_documents,
    fetch_agenda_xml,
    fetch_committee_page,
    parse_agenda_matters,
)


HTML = """
<script>
{edktunnus:"EDK-2026-AK-1",eduskuntatunnus:"TaVE 40/2026 vp",asiakirjatyyppinimi:"Esityslista",asiakirjatyyppikoodi:"TaVE",nimeketeksti:"Tiistai 28.4.2026 klo 12.00",valiokuntanimi:null,laadintapvm:"2026-04-28",viimeisinJulkaisuajankohta:"2026-04-24T11:21:24.988+00:00"}
{edktunnus:"EDK-2026-AK-2",eduskuntatunnus:"TaVP 39/2026 vp",asiakirjatyyppinimi:"Pöytäkirja",asiakirjatyyppikoodi:"TaVP",nimeketeksti:"Pöytäkirja",valiokuntanimi:null,laadintapvm:"2026-04-21",viimeisinJulkaisuajankohta:"2026-04-22T13:09:22.227+00:00"}
{edktunnus:"EDK-2026-AK-3",eduskuntatunnus:null,asiakirjatyyppinimi:"Viikkosuunnitelma",asiakirjatyyppikoodi:"VS",nimeketeksti:"Viikkosuunnitelma viikolle 18/2026",valiokuntanimi:null,laadintapvm:"2026-04-24",viimeisinJulkaisuajankohta:"2026-04-24T11:21:24.988+00:00"}
{edktunnus:"EDK-2026-AK-4",eduskuntatunnus:null,asiakirjatyyppinimi:"Kokoussuunnitelma",asiakirjatyyppikoodi:"KS",nimeketeksti:"Kokoussuunnitelma",valiokuntanimi:null,laadintapvm:"2026-02-06",viimeisinJulkaisuajankohta:"2026-04-24T09:48:28.897+00:00"}
</script>
"""


XML = """
<root xmlns:vsk="http://www.eduskunta.fi/skeemat/vaskikooste/2011/01/04"
      xmlns:met1="http://www.vn.fi/skeemat/metatietoelementit/2010/04/27">
  <vsk:Asiakohta>
    <vsk:KohtaNimeke>
      <met1:NimekeTeksti>Hallituksen esitys kuluttajansuojasta</met1:NimekeTeksti>
    </vsk:KohtaNimeke>
    <vsk:KohtaAsia>
      <met1:AsiakirjatyyppiNimi>Hallituksen esitys</met1:AsiakirjatyyppiNimi>
      <met1:EduskuntaTunnus>HE 37/2026 vp</met1:EduskuntaTunnus>
    </vsk:KohtaAsia>
    <vsk:KohtaAsiakirja>
      <met1:AsiakirjatyyppiNimi>Valiokunnan lausunto</met1:AsiakirjatyyppiNimi>
      <met1:EduskuntaTunnus>PeVL 17/2026 vp</met1:EduskuntaTunnus>
    </vsk:KohtaAsiakirja>
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
  <vsk:MuuAsiakohta>
    <met1:NimekeTeksti>Muut asiat</met1:NimekeTeksti>
  </vsk:MuuAsiakohta>
</root>
"""


def test_extract_documents_finds_committee_document_types() -> None:
    documents = extract_documents(HTML)

    assert [doc.tyyppikoodi for doc in documents] == ["TaVE", "TaVP", "VS", "KS"]
    assert documents[0].eduskuntatunnus == "TaVE 40/2026 vp"
    assert documents[2].eduskuntatunnus is None


def test_parse_agenda_matters_returns_only_scheduled_matters() -> None:
    matters = parse_agenda_matters(XML)

    assert [matter.eduskuntatunnus for matter in matters] == ["HE 37/2026 vp", "U 27/2026 vp"]
    assert matters[0].title == "Hallituksen esitys kuluttajansuojasta"
    assert matters[0].type == "Hallituksen esitys"


@pytest.mark.parametrize(
    "eduskuntatunnus, expected",
    [
        ("HE 61/2026 vp", "https://www.eduskunta.fi/valtiopaivaasiat/HE+61/2026"),
        (" U 27/2026 vp ", "https://www.eduskunta.fi/valtiopaivaasiat/U+27/2026"),
        ("", ""),
    ],
)
def test_build_matter_url(eduskuntatunnus, expected) -> None:
    assert build_matter_url(eduskuntatunnus) == expected


def test_fetch_committee_page_sends_browser_user_agent() -> None:
    class _Transport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            assert str(request.url) == "https://example.invalid/tav"
            assert request.headers["user-agent"] == HEADERS["User-Agent"]
            return httpx.Response(200, text="<html>ok</html>")

    with httpx.Client(transport=_Transport()) as client:
        body = fetch_committee_page(client, "https://example.invalid/tav")

    assert body == "<html>ok</html>"


def test_fetch_agenda_xml_picks_latest_by_created() -> None:
    response = {
        "columnNames": ["Id", "XmlData", "Created", "Eduskuntatunnus"],
        "rowData": [
            [1, "<old/>", "2026-04-20 10:00:00", "TaVE 1/2026 vp"],
            [2, "<latest/>", "2026-04-22 09:30:00", "TaVE 1/2026 vp"],
        ],
    }

    class _Transport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            assert str(request.url).split("?")[0] == VASKI_URL
            assert request.url.params["columnName"] == "Eduskuntatunnus"
            assert request.url.params["columnValue"] == "TaVE 1/2026 vp"
            assert request.headers["user-agent"] == HEADERS["User-Agent"]
            return httpx.Response(200, json=response)

    with httpx.Client(transport=_Transport()) as client:
        xml = fetch_agenda_xml(client, "TaVE 1/2026 vp")

    assert xml == "<latest/>"


def test_fetch_agenda_xml_raises_on_empty_rows() -> None:
    response = {"columnNames": ["Id", "XmlData", "Created", "Eduskuntatunnus"], "rowData": []}

    class _Transport(httpx.BaseTransport):
        def handle_request(self, _request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=response)

    with httpx.Client(transport=_Transport()) as client:
        with pytest.raises(LookupError, match="No VaskiData rows"):
            fetch_agenda_xml(client, "TaVE 999/2026 vp")
