from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import httpx

VASKI_URL = "https://avoindata.eduskunta.fi/api/v1/tables/VaskiData/rows"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; lausuntobotti/1.0)"}

NS = {
    "vaski": "http://www.eduskunta.fi/skeemat/vaskikooste/2011/01/04",
    "meta": "http://www.vn.fi/skeemat/metatietoelementit/2010/04/27",
}

ITEM_RE = re.compile(
    r'\{edktunnus:"(?P<edktunnus>[^"]+)"'
    r',eduskuntatunnus:(?:null|"(?P<eduskuntatunnus>[^"]*)")'
    r',asiakirjatyyppinimi:"(?P<tyyppinimi>[^"]+)"'
    r',asiakirjatyyppikoodi:"(?P<tyyppikoodi>[^"]+)"'
    r'.*?nimeketeksti:"(?P<nimeke>[^"]+)"'
    r'.*?laadintapvm:"(?P<pvm>[^"]+)"'
    r'.*?viimeisinJulkaisuajankohta:"(?P<julkaistu>[^"]+)"',
    re.DOTALL,
)


@dataclass(frozen=True)
class Document:
    """A document item embedded on a committee page."""

    edktunnus: str
    eduskuntatunnus: str | None
    tyyppikoodi: str
    nimeke: str
    laadintapvm: str
    julkaistu: str


@dataclass(frozen=True)
class Matter:
    """A single matter scheduled on a committee agenda."""

    eduskuntatunnus: str
    title: str
    type: str


def fetch_committee_page(client: httpx.Client, url: str) -> str:
    response = client.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def extract_documents(html: str) -> list[Document]:
    """Extract VS, KS, esityslista, and pöytäkirja items from a committee page."""
    return [
        Document(
            edktunnus=match.group("edktunnus"),
            eduskuntatunnus=match.group("eduskuntatunnus"),
            tyyppikoodi=match.group("tyyppikoodi"),
            nimeke=match.group("nimeke"),
            laadintapvm=match.group("pvm"),
            julkaistu=match.group("julkaistu"),
        )
        for match in ITEM_RE.finditer(html)
    ]


def fetch_agenda_xml(client: httpx.Client, eduskuntatunnus: str) -> str:
    """Fetch the latest VaskiData XML for an agenda by its parliamentary code."""
    response = client.get(
        VASKI_URL,
        params={
            "columnName": "Eduskuntatunnus",
            "columnValue": eduskuntatunnus,
            "page": "0",
            "perPage": "10",
        },
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    rows = data.get("rowData") or []
    if not rows:
        raise LookupError(f"No VaskiData rows for {eduskuntatunnus!r}")

    columns = data["columnNames"]

    def _row_dict(row: list[object]) -> dict[str, object]:
        return dict(zip(columns, row, strict=True))

    def _created(row: dict[str, object]) -> str:
        value = row.get("Created")
        return value if isinstance(value, str) else ""

    latest = max((_row_dict(row) for row in rows), key=_created)
    xml_data = latest.get("XmlData")
    if not isinstance(xml_data, str):
        raise LookupError(f"No XmlData in VaskiData row for {eduskuntatunnus!r}")
    return xml_data


def parse_agenda_matters(xml: str) -> list[Matter]:
    """Extract actual scheduled matters from an esityslista XML."""
    root = ET.fromstring(xml)
    matters = []
    for item in root.iter(f"{{{NS['vaski']}}}Asiakohta"):
        tunnus = item.findtext("vaski:KohtaAsia/meta:EduskuntaTunnus", namespaces=NS)
        if not tunnus:
            continue
        title = item.findtext("vaski:KohtaNimeke/meta:NimekeTeksti", namespaces=NS) or ""
        type_name = item.findtext(
            "vaski:KohtaAsia/meta:AsiakirjatyyppiNimi",
            namespaces=NS,
        )
        matters.append(
            Matter(
                eduskuntatunnus=tunnus.strip(),
                title=title.strip(),
                type=(type_name or "").strip(),
            )
        )
    return matters
