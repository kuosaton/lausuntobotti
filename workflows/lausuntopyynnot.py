from __future__ import annotations

import os
import sys
from datetime import UTC, datetime

import httpx

import config
from clients.lausuntopalvelu import Proposal, fetch_recent, get_participation_flags
from delivery.email import build_lausuntopyynto_digest, send_email
from processing.llm_scorer import score_item
from processing.score_classification import classify_score
from state_store import (
    _append_flagged,
    _append_log,
    _load_json,
    _migrate_score_log_split,
    _save_json,
)

_LOG_SOURCE_LAUSUNTOPALVELU = "lausuntopalvelu"


def _score_proposal(client: httpx.Client, proposal: Proposal, ctx: dict) -> dict | None:
    on_distribution_list = False
    has_responded = False
    try:
        on_distribution_list, has_responded = get_participation_flags(
            client, proposal.id, "Kuluttajaliit"
        )
    except httpx.HTTPError as exc:
        print(
            f"  [WARN] could not read participation info for {proposal.id}: {exc}",
            file=sys.stderr,
        )

    if on_distribution_list:
        print(f"  [SKIP DISTRIBUTION] {proposal.title}")
        return {"_skip_reason": "jakelu", "jakelu_kuluttajaliitto": True}

    if has_responded:
        print(f"  [SKIP RESPONDED] {proposal.title}")
        return {"_skip_reason": "already_responded", "jakelu_kuluttajaliitto": False}

    try:
        result = score_item(proposal.title, proposal.abstract, "lausuntopalvelu", ctx)
    except Exception as exc:
        print(f"  [ERROR] scoring failed for {proposal.id}: {exc}", file=sys.stderr)
        return None

    result["jakelu_kuluttajaliitto"] = False
    return result


def _build_scored_entry(p: Proposal, result: dict, timestamp: str) -> dict:
    return {
        "timestamp": timestamp,
        "source": _LOG_SOURCE_LAUSUNTOPALVELU,
        "id": p.id,
        "title": p.title,
        "score": result["score"],
        "rationale": result.get("rationale", ""),
        "themes": result.get("themes", []),
        "jakelu_kuluttajaliitto": result["jakelu_kuluttajaliitto"],
        "published_on": p.published_on.isoformat(),
        "deadline": p.deadline.date().isoformat() if p.deadline else None,
        "organization": p.organization_name,
        "url": p.url,
    }


def _record_result(p: Proposal, result: dict, notified: bool, seen: dict) -> None:
    now = datetime.now(UTC).isoformat()
    seen[p.id] = {
        "first_seen": now,
        "title": p.title,
        "score": result["score"],
        "notified": notified,
        "notified_at": now if notified else None,
        "published_on": p.published_on.isoformat(),
    }
    log_entry = _build_scored_entry(p, result, now)
    log_entry["notified"] = notified
    _append_log(log_entry)


def _deliver_digest(
    flagged: list[dict], dry_run: bool, borderline: list[dict] | None = None
) -> bool:
    borderline = borderline or []
    if flagged:
        print(f"\n{len(flagged)} item(s) above threshold:")
        for item in sorted(flagged, key=lambda x: -x["score"]):
            print(f"  [{item['score']}/10] {item['proposal'].title}")
    if borderline:
        print(f"\n{len(borderline)} borderline item(s) (score 4-5):")
        for item in sorted(borderline, key=lambda x: -x["score"]):
            print(f"  [{item['score']}/10] {item['proposal'].title}")
    subject, html_body, text_body = build_lausuntopyynto_digest(flagged, borderline)
    print(f"\nSubject: {subject}")
    print(text_body)
    if dry_run:
        print("\n--- DRY RUN: would send email ---")
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
    print(f"Email sent to {recipient}")
    return True


def _score_lausuntopyynto_proposals(
    new_proposals: list[Proposal],
    ctx: dict,
    seen: dict,
) -> tuple[list[dict], list[dict], list[tuple[Proposal, dict]]]:
    flagged = []
    borderline = []
    scored_results: list[tuple[Proposal, dict]] = []

    with httpx.Client() as client:
        for p in new_proposals:
            result = _score_proposal(client, p, ctx)
            if result is None:
                continue

            skip_reason = result.get("_skip_reason")
            if skip_reason in ("jakelu", "already_responded"):
                now = datetime.now(UTC).isoformat()
                seen[p.id] = {
                    "first_seen": now,
                    "title": p.title,
                    "score": 0,
                    "notified": False,
                    "notified_at": None,
                    "status": f"skipped_{skip_reason}",
                    "published_on": p.published_on.isoformat(),
                }
                continue

            score = result["score"]
            scored_results.append((p, result))

            band = classify_score(score)
            if band == "flag":
                print(f"  [FLAG {score}/10] {p.title}")
                flagged.append({"proposal": p, **result})
            elif band == "log":
                print(f"  [LOG {score}/10] {p.title}")
                borderline.append({"proposal": p, **result})
            else:
                print(f"  [DROP {score}/10] {p.title}")

    return flagged, borderline, scored_results


def _record_lausuntopyynto_results(
    scored_results: list[tuple[Proposal, dict]],
    digest_sent: bool,
    seen: dict,
) -> None:
    for p, result in scored_results:
        notified = classify_score(result["score"]) == "flag" and digest_sent
        _record_result(p, result, notified, seen)
        if classify_score(result["score"]) == "flag":
            flagged_entry = _build_scored_entry(p, result, datetime.now(UTC).isoformat())
            _append_flagged(flagged_entry)


def cmd_lausuntopyynnot(dry_run: bool, ctx: dict | None = None) -> None:
    _migrate_score_log_split()
    if ctx is None:
        print("Aborted.")
        return

    seen = _load_json(config.SEEN_PROPOSALS_PATH)

    print("Fetching lausuntopalvelu proposals...", flush=True)
    with httpx.Client() as client:
        proposals = fetch_recent(client, top=config.LAUSUNTOPALVELU_FETCH_TOP)

    today = datetime.now(UTC).date()
    open_proposals = [p for p in proposals if p.deadline is None or p.deadline.date() >= today]
    new_proposals = [p for p in open_proposals if p.id not in seen]
    print(f"  {len(proposals)} fetched, {len(open_proposals)} open, {len(new_proposals)} new")

    if not new_proposals:
        print("Nothing new to score.")
        return

    answer = input(f"Score {len(new_proposals)} proposal(s)? [Y/n] ").strip().lower()
    if answer not in ("", "y"):
        print("Aborted.")
        return

    flagged, borderline, scored_results = _score_lausuntopyynto_proposals(new_proposals, ctx, seen)

    if not flagged and not borderline:
        _record_lausuntopyynto_results(scored_results, digest_sent=False, seen=seen)
        _save_json(config.SEEN_PROPOSALS_PATH, seen)
        print("No items above log threshold.")
        return

    digest_sent = _deliver_digest(flagged, dry_run, borderline=borderline)
    _record_lausuntopyynto_results(scored_results, digest_sent, seen)
    _save_json(config.SEEN_PROPOSALS_PATH, seen)
