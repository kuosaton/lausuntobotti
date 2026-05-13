from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from datetime import date as date_type
from pathlib import Path
from types import SimpleNamespace
from typing import TypedDict

import httpx
from dotenv import load_dotenv

import config
from clients.kuluttajaliitto import build_context, fetch_statements
from clients.lausuntopalvelu import Proposal, fetch_recent, get_participation_flags
from delivery.email import build_daily_digest, send_email
from processing.llm_scorer import score_item

load_dotenv()


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------


def _read_json[T](path: Path, default: T) -> T:
    if path.exists() and path.stat().st_size > 2:
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def _load_json(path: Path) -> dict:
    return _read_json(path, {})


def _write_json_atomic(path: Path, data: object) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _save_json(path: Path, data: dict) -> None:
    _write_json_atomic(path, data)


def _append_log(entry: dict) -> None:
    with config.SCORE_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _append_flagged(entry: dict) -> None:
    path = config.FLAGGED_PATH
    items = _read_json(path, [])
    items.append(entry)
    _write_json_atomic(path, items)


def _load_context() -> dict:
    return _read_json(config.CONTEXT_PATH, {"last_updated": None, "recent_statements": []})


def _save_context(ctx: dict) -> None:
    _write_json_atomic(config.CONTEXT_PATH, ctx)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_update_context() -> None:
    print("Fetching Kuluttajaliitto lausunnot...", flush=True)
    with httpx.Client() as client:
        statements = fetch_statements(client, per_page=100)
    new_ctx = build_context(statements)
    existing = _load_context()
    if new_ctx["recent_statements"] == existing["recent_statements"]:
        print(f"Context unchanged ({len(statements)} statements, already up to date).")
        return
    _save_context(new_ctx)
    print(f"Saved {len(statements)} statements to {config.CONTEXT_PATH}")


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
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"  [ERROR] scoring failed for {proposal.id}: {exc}", file=sys.stderr)
        return None

    result["jakelu_kuluttajaliitto"] = False
    return result


def _build_scored_entry(p: Proposal, result: dict, timestamp: str) -> dict:
    return {
        "timestamp": timestamp,
        "source": "lausuntopalvelu",
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
) -> None:
    borderline = borderline or []
    if flagged:
        print(f"\n{len(flagged)} item(s) above threshold:")
        for item in sorted(flagged, key=lambda x: -x["score"]):
            print(f"  [{item['score']}/10] {item['proposal'].title}")
    if borderline:
        print(f"\n{len(borderline)} borderline item(s) (score 4-5):")
        for item in sorted(borderline, key=lambda x: -x["score"]):
            print(f"  [{item['score']}/10] {item['proposal'].title}")
    subject, html_body, text_body = build_daily_digest(flagged, borderline)
    print(f"\nSubject: {subject}")
    print(text_body)
    if dry_run:
        print("\n--- DRY RUN: would send email ---")
        return
    recipient = os.environ.get("RECIPIENT_EMAIL", "?")
    answer = input(f"\nSend to {recipient}? [Y/n] ").strip().lower()
    if answer != "y":
        print("Aborted.")
        return
    send_email(subject=subject, html_body=html_body, text_body=text_body)
    print(f"Email sent to {recipient}")


def cmd_daily(dry_run: bool) -> None:
    ctx = _load_context()
    if not ctx["recent_statements"]:
        print(
            "WARNING: Kuluttajaliitto context is empty. Run --update-context first.",
            file=sys.stderr,
        )

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
    if answer != "y":
        print("Aborted.")
        return

    flagged = []
    borderline = []

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
            notified = score >= config.NOTIFY_THRESHOLD and not dry_run

            _record_result(p, result, notified, seen)

            if score >= config.NOTIFY_THRESHOLD:
                print(f"  [FLAG {score}/10] {p.title}")
                flagged.append({"proposal": p, **result})
                flagged_entry = _build_scored_entry(p, result, datetime.now(UTC).isoformat())
                _append_flagged(flagged_entry)
            elif score >= config.LOG_THRESHOLD:
                print(f"  [LOG {score}/10] {p.title}")
                borderline.append({"proposal": p, **result})
            else:
                print(f"  [DROP {score}/10] {p.title}")

    _save_json(config.SEEN_PROPOSALS_PATH, seen)

    if not flagged and not borderline:
        print("No items above log threshold.")
        return

    _deliver_digest(flagged, dry_run, borderline=borderline)


def cmd_weekly(dry_run: bool) -> None:  # pylint: disable=unused-argument
    print(
        "Weekly committee digest is not yet implemented (planned for version 0.3.0).",
        file=sys.stderr,
    )
    sys.exit(1)


def cmd_midweek(dry_run: bool) -> None:  # pylint: disable=unused-argument
    print(
        "Midweek committee check is not yet implemented (planned for version 0.3.0).",
        file=sys.stderr,
    )
    sys.exit(1)


def _read_borderline_entries(days: int = 7) -> list[dict]:
    """Return raw score-log dicts for borderline items within the last `days` days."""
    if not config.SCORE_LOG_PATH.exists():
        return []
    cutoff = datetime.now(UTC).timestamp() - days * 86400
    entries = []
    with config.SCORE_LOG_PATH.open(encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            score = entry.get("score", 0)
            ts = datetime.fromisoformat(entry["timestamp"].rstrip("Z")).replace(tzinfo=UTC)
            if ts.timestamp() < cutoff:
                continue
            if config.LOG_THRESHOLD <= score < config.NOTIFY_THRESHOLD:
                entries.append(entry)
    return entries


def _parse_date_only(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = date_type.fromisoformat(value)
    except ValueError:
        return None
    return datetime(parsed.year, parsed.month, parsed.day)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _build_proposal(entry: dict, *, skip_expired: bool) -> SimpleNamespace | None:
    deadline = _parse_date_only(entry.get("deadline"))
    published_on = _parse_datetime(entry.get("published_on"))
    if skip_expired and deadline is not None:
        today = datetime.now(UTC).date()
        if deadline.date() < today:
            return None
    return SimpleNamespace(
        title=entry.get("title", ""),
        organization_name=entry.get("organization") or "-",
        deadline=deadline,
        published_on=published_on,
        url=entry.get("url", ""),
    )


def cmd_review_logged(days: int = 7) -> None:
    entries = _read_borderline_entries(days)
    if not entries:
        print(f"No borderline items in the last {days} days.")
        return
    print(
        f"--- LOGGED ({len(entries)} items, score {config.LOG_THRESHOLD}-{config.NOTIFY_THRESHOLD - 1}) ---\n"
    )
    for entry in entries:
        print(f"[{entry['score']}/10] {entry['timestamp'][:10]}  {entry['title']}")
        print(f"  {entry.get('rationale', '')}")
        print()


def _load_flagged() -> list[dict]:
    if not config.FLAGGED_PATH.exists() or config.FLAGGED_PATH.stat().st_size <= 2:
        return []
    items = json.loads(config.FLAGGED_PATH.read_text(encoding="utf-8"))
    flagged = []
    for e in items:
        proposal = _build_proposal(e, skip_expired=True)
        if proposal is None:
            continue
        flagged.append(
            {
                "proposal": proposal,
                "score": e.get("score", 0),
                "rationale": e.get("rationale", ""),
                "themes": e.get("themes", []),
            }
        )
    return flagged


def _load_borderline(days: int = 7) -> list[dict]:
    """Return borderline items from the score log within the last `days` days."""
    items = []
    for entry in _read_borderline_entries(days):
        proposal = _build_proposal(entry, skip_expired=False)
        if proposal is None:
            continue
        items.append(
            {
                "proposal": proposal,
                "score": entry.get("score", 0),
                "rationale": entry.get("rationale", ""),
                "themes": entry.get("themes", []),
            }
        )
    return items


def cmd_preview_digest(days: int = 7) -> None:
    """Print the current digest (flagged + recent borderline) as plain text."""
    flagged = _load_flagged()
    borderline = _load_borderline(days=days)
    if not flagged and not borderline:
        print("Nothing to preview: no flagged items and no borderline items in the score log.")
        return
    subject, _html_body, text_body = build_daily_digest(flagged, borderline)
    print(f"Subject: {subject}\n")
    print(text_body)


def cmd_resend_digest(dry_run: bool, days: int = 7) -> None:
    """Resend the digest (flagged + recent borderline) without re-running scoring."""
    flagged = _load_flagged()
    borderline = _load_borderline(days=days)
    if not flagged and not borderline:
        print("Nothing to send: no flagged items and no borderline items in the score log.")
        return
    _deliver_digest(flagged, dry_run, borderline=borderline)


def cmd_reset_state() -> None:
    print("This will erase all state: seen proposals, score log, and flagged items.")
    answer = input("Continue? [y/N] ").strip().lower()
    if answer != "y":
        print("Aborted.")
        return
    _save_json(config.SEEN_PROPOSALS_PATH, {})
    _save_json(config.SEEN_DOCUMENTS_PATH, {})
    config.FLAGGED_PATH.write_text("[]", encoding="utf-8")
    config.SCORE_LOG_PATH.write_text("", encoding="utf-8")
    print("State reset.")


def _run_daily(dry_run: bool) -> None:
    cmd_daily(dry_run=dry_run)


def _run_weekly(dry_run: bool) -> None:
    cmd_weekly(dry_run=dry_run)


def _run_midweek(dry_run: bool) -> None:
    cmd_midweek(dry_run=dry_run)


def _run_review(days: int) -> None:
    cmd_review_logged(days=days)


def _run_preview(days: int | None = None) -> None:
    if days is None:
        cmd_preview_digest()
    else:
        cmd_preview_digest(days=days)


def _run_resend(dry_run: bool, days: int | None = None) -> None:
    if days is None:
        cmd_resend_digest(dry_run=dry_run)
    else:
        cmd_resend_digest(dry_run=dry_run, days=days)


def _dispatch_cli(args: argparse.Namespace) -> None:
    actions: list[tuple[str, Callable[[], None]]] = [
        ("update_context", cmd_update_context),
        ("daily", lambda: _run_daily(args.dry_run)),
        ("weekly", lambda: _run_weekly(args.dry_run)),
        ("midweek", lambda: _run_midweek(args.dry_run)),
        ("review_logged", lambda: _run_review(args.days)),
        ("preview_digest", lambda: _run_preview(args.days)),
        ("resend_digest", lambda: _run_resend(args.dry_run, args.days)),
        ("reset_state", cmd_reset_state),
        ("interactive", cmd_interactive),
    ]
    for flag, action in actions:
        if getattr(args, flag):
            action()


# ---------------------------------------------------------------------------
# Interactive UI
# ---------------------------------------------------------------------------


class _MenuItem(TypedDict):
    key: str
    label: str
    description: list[str]
    action: Callable[[], None] | None


def _menu_items() -> list[_MenuItem]:
    return [
        {
            "key": "1",
            "label": "Daily check",
            "description": [
                "Fetch new lausuntopalvelu proposals, score with Claude,",
                "and send email digest (flagged + borderline).",
            ],
            "action": lambda: _run_daily(dry_run=False),
        },
        {
            "key": "2",
            "label": "Daily check (dry run)",
            "description": ["Same as above but print the digest instead of sending."],
            "action": lambda: _run_daily(dry_run=True),
        },
        {
            "key": "3",
            "label": "Update Kuluttajaliitto context",
            "description": [
                "Re-fetch Kuluttajaliitto published statements used as",
                "scoring context. Run this before the first daily check",
                "and periodically to keep context current.",
            ],
            "action": cmd_update_context,
        },
        {
            "key": "4",
            "label": "Review borderline items (7 days)",
            "description": [
                "Print borderline items (score 4-5) from the last 7 days",
                "as a raw list for manual calibration review.",
            ],
            "action": lambda: _run_review(days=7),
        },
        {
            "key": "5",
            "label": "Review borderline items (custom range)",
            "description": ["Same as above with a custom day range."],
            "action": _menu_review_custom,
        },
        {
            "key": "6",
            "label": "Preview digest",
            "description": [
                "Print the current digest (flagged items + borderline",
                "from the last 7 days) as plain text. No email sent.",
            ],
            "action": _run_preview,
        },
        {
            "key": "7",
            "label": "Resend digest",
            "description": [
                "Send the digest email (flagged + borderline from the",
                "last 7 days) without re-running scoring. Useful for",
                "testing email delivery or resending after a failure.",
            ],
            "action": lambda: _run_resend(dry_run=False),
        },
        {
            "key": "8",
            "label": "Reset state",
            "description": [
                "Erase all state files (seen proposals, score log,",
                "flagged items) and start fresh.",
            ],
            "action": cmd_reset_state,
        },
        {
            "key": "h",
            "label": "Help",
            "description": ["Show this help."],
            "action": None,
        },
        {
            "key": "0",
            "label": "Exit",
            "description": ["Exit."],
            "action": None,
        },
    ]


def _format_menu(items: list[_MenuItem]) -> str:
    lines = ["Lausuntobotti", "─────────────────────────────────────"]
    for item in items:
        lines.append(f"{item['key']}  {item['label']}")
    lines.append("─────────────────────────────────────")
    return "\n".join(lines)


def _format_help(items: list[_MenuItem]) -> str:
    lines = ["Option descriptions:"]
    label_width = max(len(item["label"]) for item in items)
    for item in items:
        desc = item["description"]
        first = desc[0] if desc else ""
        prefix = f"  {item['key']}  "
        lines.append(f"{prefix}{item['label']:<{label_width}}{first}")
        indent = " " * (len(prefix) + label_width)
        for extra in desc[1:]:
            lines.append(f"{indent}{extra}")
    return "\n".join(lines)


def _menu_review_custom() -> None:
    raw = input("Days to look back: ").strip()
    try:
        _run_review(days=int(raw))
    except ValueError:
        print(f"Invalid number: {raw!r}")


def cmd_interactive() -> None:
    items = _menu_items()
    actions = {item["key"]: item["action"] for item in items if item["action"] is not None}
    menu_text = _format_menu(items)
    help_text = _format_help(items)
    print(menu_text)
    while True:
        try:
            choice = input("> ").strip()
        except EOFError, KeyboardInterrupt:
            print()
            break

        if choice == "0":
            break
        if choice == "h":
            print(help_text)
            continue
        action = actions.get(choice)
        if action is None:
            print(help_text)
            continue
        action()
        print(menu_text)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Lausuntobotti – Kuluttajaliitto monitoring tool")
    parser.add_argument("--daily", action="store_true", help="Run daily lausuntopalvelu check")
    parser.add_argument(
        "--weekly", action="store_true", help="Run weekly committee digest (Fridays)"
    )
    parser.add_argument(
        "--midweek", action="store_true", help="Run mid-week committee update check"
    )
    parser.add_argument(
        "--update-context",
        action="store_true",
        help="Refresh Kuluttajaliitto context from their website",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Score items and log them, but do not send email",
    )
    parser.add_argument(
        "--review-logged",
        action="store_true",
        help="Print borderline (score 4-5) items as a raw list for calibration review",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Days to look back for --review-logged and --preview-digest / --resend-digest (default: 7)",
    )
    parser.add_argument(
        "--preview-digest",
        action="store_true",
        help="Print current digest (flagged + recent borderline) as plain text, no email",
    )
    parser.add_argument(
        "--resend-digest",
        action="store_true",
        help="Send digest email (flagged + recent borderline) without re-running scoring",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Erase all state files and start fresh",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Launch interactive menu",
    )
    args = parser.parse_args()

    if not any(
        [
            args.daily,
            args.weekly,
            args.midweek,
            args.update_context,
            args.review_logged,
            args.preview_digest,
            args.resend_digest,
            args.reset_state,
            args.interactive,
        ]
    ):
        cmd_interactive()
        return

    _dispatch_cli(args)


if __name__ == "__main__":
    main()
