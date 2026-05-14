from __future__ import annotations

import os
from datetime import date, datetime
from typing import TypedDict

import resend

from config import COMMITTEE_DISPLAY_NAMES


def _fmt_date(d: date | datetime) -> str:
    return f"{d.day}.{d.month}.{d.year}"


def _deadline_info(deadline: date | datetime | None) -> tuple[str, int] | None:
    if deadline is None:
        return None
    d = deadline.date() if isinstance(deadline, datetime) else deadline
    days = (d - date.today()).days
    return _fmt_date(deadline), days


def _deadline_display(deadline: date | datetime | None) -> str:
    info = _deadline_info(deadline)
    if info is None:
        return "-"
    date_str, days = info
    if days > 0:
        return f"{date_str} ({days} pv)"
    if days == 0:
        return f"{date_str} (tänään)"
    return date_str


def _deadline_html(deadline: date | datetime | None) -> str:
    info = _deadline_info(deadline)
    if info is None:
        return "-"
    date_str, days = info
    if days <= 0:
        return date_str
    if days <= 7:
        style = "color:#c0392b;font-weight:bold;"
    elif days <= 14:
        style = "color:#e67e22;"
    else:
        style = "color:#888;"
    return f'{date_str} <span style="{style}">({days} pv)</span>'


def _footer_html() -> str:
    return (
        '  <p style="font-size:11px;color:#aaa;">\n'
        "    Lausuntobotti &middot; "
        '<a href="https://github.com/kuosaton/lausuntobotti" target="_blank" '
        'style="color:#aaa;">GitHub</a>\n'
        "    &middot; Palautetta, kommentteja? Voit vastata suoraan tähän viestiin.\n"
        "  </p>"
    )


def send_email(subject: str, html_body: str, text_body: str = "") -> str:
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    if not api_key:
        raise ValueError("RESEND_API_KEY is required")

    sender_email = os.environ.get("SENDER_EMAIL", "").strip()
    if not sender_email:
        raise ValueError("SENDER_EMAIL is required")

    recipients = [a.strip() for a in os.environ.get("RECIPIENT_EMAIL", "").split(",") if a.strip()]
    if not recipients:
        raise ValueError("RECIPIENT_EMAIL must include at least one recipient")

    resend.api_key = api_key

    params: resend.Emails.SendParams = {
        "from": sender_email,
        "to": recipients,
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }

    result = resend.Emails.send(params)
    email_id = result.get("id")
    if not isinstance(email_id, str) or not email_id:
        raise ValueError("Resend response did not include an email id")
    return email_id


# ---------------------------------------------------------------------------
# Daily lausuntopalvelu digest
# ---------------------------------------------------------------------------


def _sort_items(items: list[dict]) -> list[dict]:
    return sorted(
        items,
        key=lambda x: (-x["score"], x["proposal"].deadline or datetime.max),
    )


class _EntryFields(TypedDict):
    title: str
    organization: str
    published_str: str
    deadline_display: str
    deadline_html: str
    themes: list[str]
    rationale: str
    score: int
    url: str


def _entry_fields(item: dict) -> _EntryFields:
    p = item["proposal"]
    published_str = _fmt_date(p.published_on) if getattr(p, "published_on", None) else "-"
    return {
        "title": p.title,
        "organization": p.organization_name,
        "published_str": published_str,
        "deadline_display": _deadline_display(p.deadline),
        "deadline_html": _deadline_html(p.deadline),
        "themes": item.get("themes", []),
        "rationale": item["rationale"],
        "score": item["score"],
        "url": p.url,
    }


def _render_text_entry(item: dict, separator: str) -> list[str]:
    fields = _entry_fields(item)
    themes = fields["themes"]
    entry = [
        separator,
        f"▸ [{fields['score']}/10] {fields['title']}",
        f"   Pyytäjä:   {fields['organization']}",
        f"   Julkaistu: {fields['published_str']}",
        f"   Määräaika: {fields['deadline_display']}",
        f"   {fields['rationale']}",
    ]
    if themes:
        entry.append(f"   Teemat:    {', '.join(themes)}")
    if fields["url"]:
        entry.append(f"   {fields['url']}")
    entry.append("")
    return entry


def _render_html_entry(item: dict) -> str:
    fields = _entry_fields(item)
    themes = ", ".join(fields["themes"])
    themes_html = (
        f'<p style="margin:4px 0 0;font-size:12px;color:#888;">Teemat: {themes}</p>'
        if themes
        else ""
    )
    return f"""
        <div style="margin-bottom:24px;padding:16px;border-left:4px solid #1a56a0;background:#f8f9fa;">
          <p style="margin:0 0 6px;font-size:15px;font-weight:bold;">
            <a href="{fields["url"]}" style="color:#1a56a0;text-decoration:none;">{fields["title"]}</a>
          </p>
          <table style="font-size:13px;color:#555;border-collapse:collapse;">
            <tr><td style="padding:2px 12px 2px 0;white-space:nowrap;">Pyytäjä</td><td>{fields["organization"]}</td></tr>
            <tr><td style="padding:2px 12px 2px 0;white-space:nowrap;">Julkaistu</td><td>{fields["published_str"]}</td></tr>
            <tr><td style="padding:2px 12px 2px 0;white-space:nowrap;">Määräaika</td><td>{fields["deadline_html"]}</td></tr>
            <tr><td style="padding:2px 12px 2px 0;white-space:nowrap;">Relevanssi</td><td>{fields["score"]}/10</td></tr>
          </table>
          <p style="margin:8px 0 0;font-size:13px;color:#333;">{fields["rationale"]}</p>
          {themes_html}
        </div>"""


def build_daily_digest(
    flagged: list[dict], borderline: list[dict] | None = None
) -> tuple[str, str, str]:
    today = _fmt_date(date.today())
    flagged_sorted = _sort_items(flagged)
    borderline_sorted = _sort_items(borderline or [])
    subject = f"Uusia lausuntopyyntöjä, {today}"
    separator = "─" * 60

    lines: list[str] = []
    if flagged_sorted:
        scores = [item["score"] for item in flagged_sorted]
        score_range = (
            f"pisteet {min(scores)}-{max(scores)}" if len(scores) > 1 else f"pistemäärä {scores[0]}"
        )
        lines.append(
            f"{len(flagged_sorted)} uutta lausuntopyyntöä, jotka saattavat kiinnostaa "
            f"Kuluttajaliittoa ({score_range}):\n"
        )
        for item in flagged_sorted:
            lines += _render_text_entry(item, separator)

    if borderline_sorted:
        if flagged_sorted:
            lines.append("")
        lines.append(f"Rajatapauksia ({len(borderline_sorted)} kpl, pistemäärä 4-5):\n")
        for item in borderline_sorted:
            lines += _render_text_entry(item, separator)
    text_body = "\n".join(lines)

    flagged_html = "".join(_render_html_entry(item) for item in flagged_sorted)
    borderline_html = "".join(_render_html_entry(item) for item in borderline_sorted)

    flagged_section_html = (
        f"""
  <h2 style="color:#1a56a0;margin-bottom:4px;">Uusia lausuntopyyntöjä</h2>
  <p style="color:#666;margin-top:0;">{today} &ndash; {len(flagged_sorted)} uutta ehdotusta</p>
  {flagged_html}"""
        if flagged_sorted
        else ""
    )
    borderline_section_html = (
        f"""
  <h2 style="color:#1a56a0;margin-bottom:4px;margin-top:32px;">Rajatapauksia</h2>
  <p style="color:#666;margin-top:0;">Pistemäärä 4-5 &ndash; {len(borderline_sorted)} kpl</p>
  {borderline_html}"""
        if borderline_sorted
        else ""
    )

    html_body = f"""<!DOCTYPE html>
<html lang="fi">
<head><meta charset="utf-8"><title>{subject}</title></head>
<body style="font-family:Arial,sans-serif;max-width:680px;margin:0 auto;padding:24px;color:#222;">{flagged_section_html}{borderline_section_html}
  <hr style="border:none;border-top:1px solid #ddd;margin:32px 0 16px;">
{_footer_html()}
</body>
</html>"""

    return subject, html_body, text_body


# ---------------------------------------------------------------------------
# Weekly committee digest
# ---------------------------------------------------------------------------


def _weekly_text_body(
    committee_items: dict[str, list[dict]],
    borderline_items: dict[str, list[dict]],
    week_number: int,
    total_flagged: int,
    summary_lines: list[str],
) -> str:
    lines = [f"Viikkokatsaus, vko {week_number}\n"]
    for key, items in committee_items.items():
        borderline = borderline_items.get(key, [])
        name = _committee_display_name(key)
        lines.append(f"--- {name.upper()} ---\n")
        if not items and not borderline:
            lines.append("Ei nostettavia asioita.\n")
            continue
        for item in items:
            fields = _weekly_entry_fields(item)
            lines += [
                f"▸ {fields['title']}",
                f"   Tunnus:     {fields['eduskuntatunnus']}",
                f"   Relevanssi: {fields['score']}/10",
                f"   {fields['rationale']}",
                f"   {fields['url']}",
                "",
            ]
        if borderline:
            lines.append("Rajatapauksia:\n")
            for item in borderline:
                fields = _weekly_entry_fields(item)
                lines += [
                    f"▸ [{fields['score']}/10] {fields['title']}",
                    f"   Tunnus:     {fields['eduskuntatunnus']}",
                    f"   {fields['rationale']}",
                    "",
                ]
    lines += summary_lines
    return "\n".join(lines)


def _weekly_html_entries(items: list[dict]) -> str:
    entries_html = ""
    for item in items:
        fields = _weekly_entry_fields(item)
        themes = ", ".join(fields["themes"])
        title_html = (
            f'<a href="{fields["url"]}" style="color:#1a56a0;text-decoration:none;">{fields["title"]}</a>'
            if fields["url"]
            else fields["title"]
        )
        entries_html += f"""
            <div style="margin-bottom:20px;padding:14px;border-left:4px solid #1a56a0;background:#f8f9fa;">
              <p style="margin:0 0 4px;font-size:14px;font-weight:bold;">
                {title_html}
              </p>
              <p style="margin:0 0 4px;font-size:12px;color:#666;">{fields["eduskuntatunnus"]}</p>
              <p style="margin:4px 0;font-size:13px;"><strong>Relevanssi:</strong> {fields["score"]}/10</p>
              <p style="margin:4px 0;font-size:13px;color:#333;">{fields["rationale"]}</p>
              {f'<p style="margin:4px 0;font-size:12px;color:#888;">Teemat: {themes}</p>' if themes else ""}
            </div>"""
    return entries_html


def _weekly_html_sections(
    committee_items: dict[str, list[dict]],
    borderline_items: dict[str, list[dict]],
) -> str:
    sections_html = ""
    for key, items in committee_items.items():
        name = _committee_display_name(key)
        borderline = borderline_items.get(key, [])
        items_html = _weekly_html_entries(items)
        if not items and not borderline:
            items_html = '<p style="color:#888;font-size:13px;">Ei nostettavia asioita.</p>'
        if borderline:
            items_html += f"""
            <p style="margin:18px 0 8px;font-size:13px;font-weight:bold;color:#666;">Rajatapauksia</p>
            {_weekly_html_entries(borderline)}"""
        sections_html += f"""
        <h3 style="color:#1a56a0;border-bottom:1px solid #ddd;padding-bottom:6px;">{name}</h3>
        {items_html}"""
    return sections_html


def _weekly_summary(
    total_scored: int,
    total_flagged: int,
    total_logged: int,
) -> tuple[list[str], str]:
    text_lines = [
        "---",
        f"Arvioitu yhteensä: {total_scored} asiaa",
        f"Nostettu: {total_flagged}",
        f"Rajatapauksia: {total_logged}",
    ]
    html = (
        '  <p style="font-size:12px;color:#888;">\n'
        f"    Arvioitu: {total_scored} asiaa &ndash; Nostettu: {total_flagged} &ndash;\n"
        f"    Rajatapauksia: {total_logged}\n"
        "  </p>"
    )
    return text_lines, html


class _WeeklyEntryFields(TypedDict):
    title: str
    url: str
    eduskuntatunnus: str
    score: int
    rationale: str
    themes: list[str]


def _weekly_entry_fields(item: dict) -> _WeeklyEntryFields:
    return {
        "title": item["title"],
        "url": item.get("url", ""),
        "eduskuntatunnus": item.get("eduskuntatunnus", "-"),
        "score": item["score"],
        "rationale": item["rationale"],
        "themes": item.get("themes", []),
    }


def _committee_display_name(key: str) -> str:
    return COMMITTEE_DISPLAY_NAMES.get(key, key)


def build_weekly_digest(
    committee_items: dict[str, list[dict]],
    week_number: int,
    total_scored: int,
    total_logged: int,
    borderline_items: dict[str, list[dict]] | None = None,
) -> tuple[str, str, str]:
    subject = f"Lausuntobotin viikkokatsaus, vko {week_number}"
    borderline_items = borderline_items or {key: [] for key in committee_items}
    total_flagged = sum(len(v) for v in committee_items.values())
    summary_lines, summary_html = _weekly_summary(total_scored, total_flagged, total_logged)
    text_body = _weekly_text_body(
        committee_items,
        borderline_items,
        week_number,
        total_flagged,
        summary_lines,
    )
    sections_html = _weekly_html_sections(committee_items, borderline_items)
    html_body = f"""<!DOCTYPE html>
<html lang="fi">
<head><meta charset="utf-8"><title>{subject}</title></head>
<body style="font-family:Arial,sans-serif;max-width:680px;margin:0 auto;padding:24px;color:#222;">
  <h2 style="color:#1a56a0;margin-bottom:4px;">Valiokuntakatsaus</h2>
  <p style="color:#666;margin-top:0;">Viikko {week_number}</p>
  {sections_html}
  <hr style="border:none;border-top:1px solid #ddd;margin:32px 0 16px;">
{summary_html}
{_footer_html()}
</body>
</html>"""
    return subject, html_body, text_body
