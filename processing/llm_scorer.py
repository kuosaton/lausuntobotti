from __future__ import annotations

import functools
import json
import re
from typing import Any, TypedDict, cast

import anthropic
from anthropic.types import CacheControlEphemeralParam, MessageParam, TextBlockParam

from config import ScoringConfig, load_scoring_config


@functools.lru_cache(maxsize=1)
def _get_client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


SYSTEM_PROMPT = """\
Olet Kuluttajaliiton avustaja, joka arvioi lausuntopalvelu.fi:n lausuntopyyntöjen \
ja eduskunnan valiokunta-asioiden relevanssia Kuluttajaliitolle.

Saat arvioitavaksi yhden asian otsikon ja kuvauksen. Saat myös tausta-aineistona \
Kuluttajaliiton verkkosivujen viimeisimmät julkaistut lausunnot.

Kuluttajaliiton tehtävän kannalta olennaista:
- Kohderyhmät ovat kuluttajat, asukkaat, potilaat ja sote-palveluiden asiakkaat.
- Työn ytimessä ovat kohderyhmien oikeudet, etu, asema, turvallisuus ja \
kohtuuhintainen, toimiva ja kestävä arki.
- Kuluttajaliitto vaikuttaa erityisesti lainsäädäntöön, muuhun sääntelyyn ja \
täytäntöönpanoon sekä tarjoaa selkeää tietoa ja neuvontaa.
- Hyvä signaali on konkreettinen vaikutus esimerkiksi kuluttajansuojaan, \
sopimussuhteisiin, hintoihin ja maksuihin, velkaantumiseen, rahoitus- ja \
maksupalveluihin, asumiseen, tuoteturvallisuuteen, välttämättömiin palveluihin, \
digitaalisiin alustoihin, huijauksiin, sote-palveluiden käyttäjien oikeuksiin tai \
kestävän kuluttamisen käytännön edellytyksiin.

Tehtäväsi:
1. Arvioi asteikolla 0-10, kuinka relevantti asia on Kuluttajaliitolle.
2. Kirjoita 1-2 lauseen perustelu suomeksi. Nimeä konkreettinen kytkös \
Kuluttajaliiton tehtävään, aiempiin lausuntoihin tai painopisteisiin.
3. Nimeä 1-3 asian keskeistä teemaa.

Vastaa AINOASTAAN JSON-muodossa, ilman muuta tekstiä:
{"score": 7, "rationale": "Koskee verkkokaupan kuluttajansuojaa EU-direktiivin \
toimeenpanossa. Kuluttajaliitto on antanut aiheesta lausuntoja aiemmin.", \
"themes": ["verkkokauppa", "kuluttajansuoja", "EU-direktiivi"]}

Pisteytysohje:
- 8-10: Asia on selvästi Kuluttajaliiton ydinaluetta ja vaikuttaa suoraan \
kohderyhmien oikeuksiin, asemaan, turvallisuuteen, palvelujen saatavuuteen tai \
kohtuuhintaiseen arkeen.
- 6-7: Asia on relevantti tai läheinen Kuluttajaliiton painopisteille, mutta \
kuluttaja-, asukas-, potilas- tai sote-asiakasvaikutus on epäsuorempi.
- 4-5: Asialla on mahdollinen mutta epävarma tai rajallinen yhteys Kuluttajaliiton \
tehtävään; kirjaa vain tarkempaa ihmisen arviota varten.
- 2-3: Yhteys on ohut, yleinen tai lähinnä välillinen.
- 0-1: Ei havaittavaa yhteyttä Kuluttajaliiton kohderyhmiin tai toimintaan.

Oikeudellinen ja käytännöllinen varovaisuus:
- Arvioi vain relevanssia, älä asian oikeudellista lopputulosta tai poliittista \
kannatettavuutta.
- Älä nosta asiaa pelkän yleisen yhteiskunnallisen merkityksen, hallinnollisen \
kiinnostavuuden tai toimialavaikutuksen vuoksi. Tarvitaan konkreettinen kytkös \
Kuluttajaliiton kohderyhmiin.
- Jos kuvaus ei anna riittävää tietoa vaikutuksesta kohderyhmiin, anna mieluummin \
matalampi pistemäärä ja sano perustelussa, mikä kytkös jäi epäselväksi.
- Kuluttajaliitto saa kutsut kuulemisiin joka tapauksessa virallisia kanavia pitkin. \
Väärät nostot heikentävät työkalun käyttökelpoisuutta, joten nosta vain, jos asia \
on selvästi relevantti.\
"""


class StatementLike(TypedDict, total=False):
    date: str
    title: str
    tags: list[str]
    excerpt: str


def _format_statements(statements: list[StatementLike]) -> str:
    lines: list[str] = []
    for s in statements:
        date = s.get("date", "")
        title = s.get("title", "")
        lines.append(f"- {date}: {title}")
        tags = s.get("tags")
        if tags:
            lines.append(f"  Teemat: {', '.join(tags)}")
        excerpt = s.get("excerpt")
        if excerpt:
            lines.append(f"  {excerpt[:450]}")
    return "\n".join(lines)


def _parse_response_json(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    candidates: list[str] = [text] if text else []

    # Accept fenced JSON outputs like ```json {...}```.
    for m in re.finditer(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL):
        fenced = m.group(1).strip()
        if fenced:
            candidates.append(fenced)

    # Fallback: take the first balanced JSON object from surrounding prose.
    start = text.find("{")
    if start != -1:
        depth = 0
        for idx in range(start, len(text)):
            ch = text[idx]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : idx + 1])
                    break

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return cast(dict[str, Any], parsed)

    preview = text[:180].replace("\n", "\\n")
    raise ValueError(f"Model response was not valid JSON object: {preview!r}")


def _cache_control(scoring_config: ScoringConfig) -> CacheControlEphemeralParam | None:
    if not scoring_config.prompt_cache:
        return None
    cache_control = CacheControlEphemeralParam(type="ephemeral")
    if scoring_config.cache_ttl == "1h":
        cache_control["ttl"] = "1h"
    return cache_control


def _cached_text_block(text: str, scoring_config: ScoringConfig) -> TextBlockParam:
    block = TextBlockParam(type="text", text=text)
    cache_control = _cache_control(scoring_config)
    if cache_control is not None:
        block["cache_control"] = cache_control
    return block


def score_item(
    title: str,
    abstract: str,
    source: str,
    context: dict[str, Any],
    signals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Score one item for relevance to Kuluttajaliitto.

    Returns {"score": int, "rationale": str, "themes": list[str]}.

    The system prompt and context block are marked for prompt caching — they stay
    identical across all calls in one run, so subsequent calls hit the cache.
    """
    scoring_config = load_scoring_config()
    context_text = _format_statements(context.get("recent_statements", []))
    item_text = (
        f"## Arvioitava asia\n\n**Lähde:** {source}\n**Otsikko:** {title}\n**Kuvaus:** {abstract}"
    )
    system_blocks = [_cached_text_block(SYSTEM_PROMPT, scoring_config)]
    message_content = [
        _cached_text_block(
            f"## Kuluttajaliiton viimeaikaiset lausunnot\n\n{context_text}",
            scoring_config,
        ),
        TextBlockParam(type="text", text=item_text),
    ]
    messages = [MessageParam(role="user", content=message_content)]
    if scoring_config.effort is None:
        response = _get_client().messages.create(
            model=scoring_config.model,
            max_tokens=scoring_config.max_tokens,
            timeout=scoring_config.timeout_seconds,
            system=system_blocks,
            messages=messages,
        )
    else:
        response = _get_client().messages.create(
            model=scoring_config.model,
            max_tokens=scoring_config.max_tokens,
            timeout=scoring_config.timeout_seconds,
            system=system_blocks,
            messages=messages,
            output_config={"effort": scoring_config.effort},
        )

    text_parts: list[str] = []
    for block in response.content:
        block_type = getattr(block, "type", None)
        text = getattr(block, "text", None)
        if block_type in (None, "text") and isinstance(text, str) and text.strip():
            text_parts.append(text)

    if not text_parts:
        raise ValueError("Anthropic response did not contain a non-empty text payload")

    return _parse_response_json("\n".join(text_parts))
