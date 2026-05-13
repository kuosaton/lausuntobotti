from __future__ import annotations

import os
from datetime import date, datetime

import resend

from config import COMMITTEE_DISPLAY_NAMES


def _fmt_date(d: date | datetime) -> str:
    return f"{d.day}.{d.month}.{d.year}"


def _deadline_display(deadline: date | datetime | None) -> str:
    if deadline is None:
        return "-"
    d = deadline.date() if isinstance(deadline, datetime) else deadline
    days = (d - date.today()).days
    date_str = _fmt_date(deadline)
    if days > 0:
        return f"{date_str} ({days} pv)"
    if days == 0:
        return f"{date_str} (tänään)"
    return date_str


def _deadline_html(deadline: date | datetime | None) -> str:
    if deadline is None:
        return "-"
    d = deadline.date() if isinstance(deadline, datetime) else deadline
    days = (d - date.today()).days
    date_str = _fmt_date(deadline)
    if days <= 0:
        return date_str
    if days <= 7:
        style = "color:#c0392b;font-weight:bold;"
    elif days <= 14:
        style = "color:#e67e22;"
    else:
        style = "color:#888;"
    return f'{date_str} <span style="{style}">({days} pv)</span>'


def send_email(subject: str, html_body: str, text_body: str = "") -> None:
    resend.api_key = os.environ.get("RESEND_API_KEY")
    sender_email = os.environ.get("SENDER_EMAIL", "")
    recipients = [a.strip() for a in os.environ.get("RECIPIENT_EMAIL", "").split(",") if a.strip()]

    params: resend.Emails.SendParams = {
        "from": sender_email,
        "to": recipients,
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }

    try:
        result = resend.Emails.send(params)
        print("Email sent successfully!")
        print(f"Email ID: {result['id']}")

    except Exception as e:
        print(f"Error sending email: {e}")


# ---------------------------------------------------------------------------
# Daily lausuntopalvelu digest
# ---------------------------------------------------------------------------


def _sort_items(items: list[dict]) -> list[dict]:
    return sorted(
        items,
        key=lambda x: (-x["score"], x["proposal"].deadline or datetime.max),
    )


def _render_text_entry(item: dict, separator: str) -> list[str]:
    p = item["proposal"]
    published_str = _fmt_date(p.published_on) if getattr(p, "published_on", None) else "-"
    deadline_str = _deadline_display(p.deadline)
    themes = item.get("themes", [])
    entry = [
        separator,
        f"▸ [{item['score']}/10] {p.title}",
        f"   Pyytäjä:   {p.organization_name}",
        f"   Julkaistu: {published_str}",
        f"   Määräaika: {deadline_str}",
        f"   {item['rationale']}",
    ]
    if themes:
        entry.append(f"   Teemat:    {', '.join(themes)}")
    if p.url:
        entry.append(f"   {p.url}")
    entry.append("")
    return entry


def _render_html_entry(item: dict) -> str:
    p = item["proposal"]
    published_str = _fmt_date(p.published_on) if getattr(p, "published_on", None) else "-"
    deadline_str = _deadline_html(p.deadline)
    themes = ", ".join(item.get("themes", []))
    themes_html = (
        f'<p style="margin:4px 0 0;font-size:12px;color:#888;">Teemat: {themes}</p>'
        if themes
        else ""
    )
    return f"""
        <div style="margin-bottom:24px;padding:16px;border-left:4px solid #1a56a0;background:#f8f9fa;">
          <p style="margin:0 0 6px;font-size:15px;font-weight:bold;">
            <a href="{p.url}" style="color:#1a56a0;text-decoration:none;">{p.title}</a>
          </p>
          <table style="font-size:13px;color:#555;border-collapse:collapse;">
            <tr><td style="padding:2px 12px 2px 0;white-space:nowrap;">Pyytäjä</td><td>{p.organization_name}</td></tr>
            <tr><td style="padding:2px 12px 2px 0;white-space:nowrap;">Julkaistu</td><td>{published_str}</td></tr>
            <tr><td style="padding:2px 12px 2px 0;white-space:nowrap;">Määräaika</td><td>{deadline_str}</td></tr>
            <tr><td style="padding:2px 12px 2px 0;white-space:nowrap;">Relevanssi</td><td>{item["score"]}/10</td></tr>
          </table>
          <p style="margin:8px 0 0;font-size:13px;color:#333;">{item["rationale"]}</p>
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
  <p style="font-size:11px;color:#aaa;">
    Lausuntobotti &middot; <a href="https://github.com/kuosaton/lausuntobotti" target="_blank" style="color:#aaa;">GitHub</a>
    &middot; Palautetta, kommentteja? Voit vastata suoraan tähän viestiin.
  </p>
</body>
</html>"""

    return subject, html_body, text_body


# ---------------------------------------------------------------------------
# Weekly committee digest
# ---------------------------------------------------------------------------


def _weekly_text_body(
    committee_items: dict[str, list[dict]],
    week_number: int,
    total_flagged: int,
    total_scored: int,
    total_logged: int,
) -> str:
    lines = [f"Viikkokatsaus, vko {week_number}\n"]
    for key, items in committee_items.items():
        name = COMMITTEE_DISPLAY_NAMES.get(key, key)
        lines.append(f"--- {name.upper()} ---\n")
        if not items:
            lines.append("Ei nostettavia asioita.\n")
        for item in items:
            lines += [
                f"▸ {item['title']}",
                f"   Tunnus:     {item.get('eduskuntatunnus', '-')}",
                f"   Relevanssi: {item['score']}/10",
                f"   {item['rationale']}",
                f"   {item.get('url', '')}",
                "",
            ]
    lines += [
        "---",
        f"Arvioitu yhteensä: {total_scored} asiaa",
        f"Nostettu: {total_flagged}",
        f"Lokitettu (pistemäärä 4-6): {total_logged}",
    ]
    return "\n".join(lines)


def _weekly_html_sections(committee_items: dict[str, list[dict]]) -> str:
    sections_html = ""
    for key, items in committee_items.items():
        name = COMMITTEE_DISPLAY_NAMES.get(key, key)
        items_html = ""
        if not items:
            items_html = '<p style="color:#888;font-size:13px;">Ei nostettavia asioita.</p>'
        for item in items:
            themes = ", ".join(item.get("themes", []))
            items_html += f"""
            <div style="margin-bottom:20px;padding:14px;border-left:4px solid #1a56a0;background:#f8f9fa;">
              <p style="margin:0 0 4px;font-size:14px;font-weight:bold;">
                {f'<a href="{item["url"]}" style="color:#1a56a0;text-decoration:none;">' if item.get("url") else ""}{item["title"]}{("</a>" if item.get("url") else "")}
              </p>
              <p style="margin:0 0 4px;font-size:12px;color:#666;">{item.get("eduskuntatunnus", "")}</p>
              <p style="margin:4px 0;font-size:13px;"><strong>Relevanssi:</strong> {item["score"]}/10</p>
              <p style="margin:4px 0;font-size:13px;color:#333;">{item["rationale"]}</p>
              {f'<p style="margin:4px 0;font-size:12px;color:#888;">Teemat: {themes}</p>' if themes else ""}
            </div>"""
        sections_html += f"""
        <h3 style="color:#1a56a0;border-bottom:1px solid #ddd;padding-bottom:6px;">{name}</h3>
        {items_html}"""
    return sections_html


def build_weekly_digest(
    committee_items: dict[str, list[dict]],
    week_number: int,
    total_scored: int,
    total_logged: int,
) -> tuple[str, str, str]:
    subject = f"Lausuntobotin viikkokatsaus, vko {week_number}"
    total_flagged = sum(len(v) for v in committee_items.values())
    text_body = _weekly_text_body(
        committee_items, week_number, total_flagged, total_scored, total_logged
    )
    sections_html = _weekly_html_sections(committee_items)
    html_body = f"""<!DOCTYPE html>
<html lang="fi">
<head><meta charset="utf-8"><title>{subject}</title></head>
<body style="font-family:Arial,sans-serif;max-width:680px;margin:0 auto;padding:24px;color:#222;">
  <h2 style="color:#1a56a0;margin-bottom:4px;">Valiokuntakatsaus</h2>
  <p style="color:#666;margin-top:0;">Viikko {week_number}</p>
  {sections_html}
  <hr style="border:none;border-top:1px solid #ddd;margin:32px 0 16px;">
  <p style="font-size:12px;color:#888;">
    Arvioitu: {total_scored} asiaa &ndash; Nostettu: {total_flagged} &ndash;
    Lokitettu (4-6): {total_logged}
  </p>
  <p style="font-size:11px;color:#aaa;">
    Lausuntobotti &middot; <a href="https://github.com/kuosaton/lausuntobotti" target="_blank" style="color:#aaa;">GitHub</a>
    &middot; Palautetta, kommentteja? Voit vastata suoraan tähän viestiin.
  </p>
</body>
</html>"""
    return subject, html_body, text_body
