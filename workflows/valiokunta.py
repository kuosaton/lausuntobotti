from __future__ import annotations

import os
import sys
from datetime import UTC, datetime

import httpx

import config
from clients.eduskunta import (
    Document,
    Matter,
    extract_documents,
    fetch_agenda_xml,
    fetch_committee_page,
    parse_agenda_matters,
)
from delivery.email import build_weekly_digest, send_email
from processing.llm_scorer import score_item
from processing.score_classification import classify_score
from state_store import _append_log, _load_json, _migrate_score_log_split, _save_json

_WEEKLY_COMMITTEES = ("talousvaliokunta",)


def _is_agenda(document: Document) -> bool:
    return document.tyyppikoodi.endswith("VE") and document.eduskuntatunnus is not None


def _collect_new_agendas(
    client: httpx.Client,
    seen_docs: dict,
) -> list[tuple[str, Document]]:
    new_agendas: list[tuple[str, Document]] = []
    for committee_key in _WEEKLY_COMMITTEES:
        url = config.COMMITTEE_URLS[committee_key]
        display = config.COMMITTEE_DISPLAY_NAMES[committee_key]
        print(f"Fetching {display}...", flush=True)
        try:
            html = fetch_committee_page(client, url)
            documents = extract_documents(html)
        except Exception as exc:
            print(f"  [ERROR] could not fetch/parse {display}: {exc}", file=sys.stderr)
            continue

        agendas = [doc for doc in documents if _is_agenda(doc) and doc.edktunnus not in seen_docs]
        print(f"  {len(documents)} documents, {len(agendas)} new agendas")
        new_agendas.extend((committee_key, agenda) for agenda in agendas)
    return new_agendas


def _resolve_agenda_matters(
    client: httpx.Client,
    agendas: list[tuple[str, Document]],
) -> list[tuple[str, Document, list[Matter]]]:
    resolved: list[tuple[str, Document, list[Matter]]] = []
    for committee_key, agenda in agendas:
        try:
            xml = fetch_agenda_xml(client, agenda.eduskuntatunnus or "")
            matters = parse_agenda_matters(xml)
        except Exception as exc:
            print(
                f"  [ERROR] could not fetch/parse {agenda.eduskuntatunnus}: {exc}",
                file=sys.stderr,
            )
            continue
        resolved.append((committee_key, agenda, matters))
    return resolved


def _mark_agenda_seen(seen_docs: dict, agenda: Document) -> None:
    seen_docs[agenda.edktunnus] = {
        "first_seen": datetime.now(UTC).isoformat(),
        "eduskuntatunnus": agenda.eduskuntatunnus,
        "nimeke": agenda.nimeke,
        "score": None,
        "matter_scores": {},
    }


def _score_weekly_matter(
    matter: Matter,
    committee_key: str,
    ctx: dict,
) -> dict | None:
    try:
        result = score_item(matter.title, matter.type, committee_key, ctx)
    except Exception as exc:
        print(f"  [ERROR] scoring {matter.eduskuntatunnus}: {exc}", file=sys.stderr)
        return None

    return result


def _record_weekly_matter(
    matter: Matter,
    committee_key: str,
    result: dict,
    notified: bool,
) -> None:
    _append_log(
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "source": committee_key,
            "id": matter.eduskuntatunnus,
            "title": matter.title,
            "score": result["score"],
            "rationale": result.get("rationale", ""),
            "themes": result.get("themes", []),
            "notified": notified,
        },
        path=config.VALIOKUNTA_SCORE_LOG_PATH,
    )


def _deliver_weekly(
    committee_items: dict[str, list[dict]],
    borderline_items: dict[str, list[dict]],
    total_scored: int,
    total_logged: int,
    dry_run: bool,
) -> bool:
    week_number = datetime.now(UTC).isocalendar().week
    total_flagged = sum(len(items) for items in committee_items.values())
    if total_flagged == 0 and total_logged == 0:
        print("No valiokunta items above log threshold.")
        return False

    subject, html_body, text_body = build_weekly_digest(
        committee_items,
        week_number,
        total_scored,
        total_logged,
        borderline_items=borderline_items,
    )
    print(f"\nSubject: {subject}")
    print(text_body)
    if dry_run:
        print(f"\n--- DRY RUN: would send valiokunta digest ({total_flagged} flagged) ---")
        return False

    recipient = os.environ.get("RECIPIENT_EMAIL", "?")
    answer = input(f"\nSend to {recipient}? [Y/n] ").strip().lower()
    if answer not in ("", "y"):
        print("Aborted.")
        return False

    try:
        send_email(subject=subject, html_body=html_body, text_body=text_body)
    except Exception as exc:
        print(f"ERROR: email delivery failed: {exc}", file=sys.stderr)
        return False
    print(f"\nWeekly digest sent: {total_flagged} flagged, {total_logged} logged")
    return True


def cmd_valiokunta(dry_run: bool, ctx: dict | None = None) -> None:  # noqa: PLR0912
    _migrate_score_log_split()
    if ctx is None:
        print("Aborted.")
        return

    seen_docs = _load_json(config.SEEN_DOCUMENTS_PATH)

    with httpx.Client() as client:
        new_agendas = _collect_new_agendas(client, seen_docs)
        if not new_agendas:
            print("No new committee agendas to process.")
            return
        agenda_matters = _resolve_agenda_matters(client, new_agendas)

    total_matters = sum(len(matters) for _, _, matters in agenda_matters)
    if total_matters == 0:
        print("No matters scheduled in the new agendas.")
        return

    answer = input(f"Score {total_matters} matter(s)? [Y/n] ").strip().lower()
    if answer not in ("", "y"):
        print("Aborted.")
        return

    committee_items: dict[str, list[dict]] = {key: [] for key in _WEEKLY_COMMITTEES}
    borderline_items: dict[str, list[dict]] = {key: [] for key in _WEEKLY_COMMITTEES}
    scored_matters: list[tuple[str, str, Matter, dict]] = []
    total_scored = 0
    total_logged = 0

    for committee_key, agenda, matters in agenda_matters:
        if agenda.edktunnus not in seen_docs:
            _mark_agenda_seen(seen_docs, agenda)
        for matter in matters:
            result = _score_weekly_matter(matter, committee_key, ctx)
            if result is None:
                continue

            score = result["score"]
            total_scored += 1
            scored_matters.append((agenda.edktunnus, committee_key, matter, result))

            band = classify_score(score)
            if band == "flag":
                print(f"  [FLAG {score}/10] {matter.eduskuntatunnus}: {matter.title}")
                committee_items[committee_key].append(
                    {
                        "title": matter.title,
                        "eduskuntatunnus": matter.eduskuntatunnus,
                        "score": score,
                        "rationale": result.get("rationale", ""),
                        "themes": result.get("themes", []),
                        "url": "",
                    }
                )
            elif band == "log":
                total_logged += 1
                print(f"  [LOG {score}/10] {matter.eduskuntatunnus}: {matter.title}")
                borderline_items[committee_key].append(
                    {
                        "title": matter.title,
                        "eduskuntatunnus": matter.eduskuntatunnus,
                        "score": score,
                        "rationale": result.get("rationale", ""),
                        "themes": result.get("themes", []),
                        "url": "",
                    }
                )
            else:
                print(f"  [DROP {score}/10] {matter.eduskuntatunnus}: {matter.title}")

    digest_sent = _deliver_weekly(
        committee_items, borderline_items, total_scored, total_logged, dry_run
    )
    for agenda_id, committee_key, matter, result in scored_matters:
        notified = classify_score(result["score"]) == "flag" and digest_sent
        _record_weekly_matter(matter, committee_key, result, notified)
        seen_docs[agenda_id]["matter_scores"][matter.eduskuntatunnus] = {
            "score": result["score"],
            "notified": notified,
        }

    _save_json(config.SEEN_DOCUMENTS_PATH, seen_docs)


def cmd_weekly(dry_run: bool) -> None:
    cmd_valiokunta(dry_run=dry_run)
