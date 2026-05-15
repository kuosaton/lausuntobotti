from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from datetime import date as date_type
from types import SimpleNamespace
from typing import TypedDict

import httpx
from dotenv import load_dotenv

import config
from clients.eduskunta import build_matter_url
from clients.kuluttajaliitto import build_context, fetch_statements
from delivery.email import build_daily_digest, build_weekly_digest, send_email
from processing.score_classification import classify_score
from state_store import (
    _load_context,
    _migrate_score_log_split,
    _save_context,
    _save_json,
    _score_log_path,
)
from workflows.lausuntopyynnot import (
    _deliver_digest,
    cmd_lausuntopyynnot as _run_lausuntopyynnot_workflow,
)
from workflows.valiokunta import cmd_valiokunta as _run_valiokunta_workflow

_SOURCE_LAUSUNTOPYYNNOT = "lausuntopyynnot"
_SOURCE_VALIOKUNTA = "valiokunta"


def _load_runtime_env() -> None:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    load_dotenv()


def _context_has_statements(ctx: dict) -> bool:
    return bool(ctx.get("recent_statements"))


def _context_is_stale(ctx: dict) -> bool:
    last_updated = _parse_datetime(ctx.get("last_updated"))
    if last_updated is None:
        return True
    if last_updated.tzinfo is None:
        last_updated = last_updated.replace(tzinfo=UTC)
    return datetime.now(UTC) - last_updated > timedelta(days=config.CONTEXT_MAX_AGE_DAYS)


def _fetch_context() -> dict:
    print("Refreshing Kuluttajaliitto context...", flush=True)
    with httpx.Client() as client:
        statements = fetch_statements(client, per_page=100)
    return build_context(statements)


def _ensure_context_fresh() -> dict | None:
    existing = _load_context()
    if _context_has_statements(existing) and not _context_is_stale(existing):
        return existing
    try:
        new_ctx = _fetch_context()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        if _context_has_statements(existing):
            print(
                f"WARNING: could not refresh Kuluttajaliitto context; using existing context: {exc}",
                file=sys.stderr,
            )
            return existing
        print(f"ERROR: could not refresh Kuluttajaliitto context: {exc}", file=sys.stderr)
        return None
    _save_context(new_ctx)
    return new_ctx


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_update_context() -> None:
    new_ctx = _fetch_context()
    existing = _load_context()
    if new_ctx["recent_statements"] == existing["recent_statements"]:
        _save_context(new_ctx)
        print("Context unchanged (already up to date).")
        return
    _save_context(new_ctx)
    print(f"Saved {len(new_ctx.get('recent_statements', []))} statements to {config.CONTEXT_PATH}")


def cmd_lausuntopyynnot(dry_run: bool) -> None:
    _run_lausuntopyynnot_workflow(dry_run=dry_run, ctx=_ensure_context_fresh())


def cmd_daily(dry_run: bool) -> None:
    cmd_lausuntopyynnot(dry_run=dry_run)


def cmd_valiokunta(dry_run: bool) -> None:
    _run_valiokunta_workflow(dry_run=dry_run, ctx=_ensure_context_fresh())


def cmd_weekly(dry_run: bool) -> None:
    cmd_valiokunta(dry_run=dry_run)


def _read_borderline_entries(days: int = 7, source: str = _SOURCE_LAUSUNTOPYYNNOT) -> list[dict]:
    """Return raw score-log dicts for borderline items within the last `days` days."""
    return [
        entry
        for entry in _read_recent_score_entries(days, source=source)
        if classify_score(entry.get("score", 0)) == "log"
    ]


def _read_recent_score_entries(days: int = 7, source: str = _SOURCE_LAUSUNTOPYYNNOT) -> list[dict]:
    """Return raw score-log dicts within the last `days` days."""
    _migrate_score_log_split()
    path = _score_log_path(source)
    if not path.exists():
        return []
    cutoff = datetime.now(UTC).timestamp() - days * 86400
    entries = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            timestamp = entry.get("timestamp")
            if not isinstance(timestamp, str):
                continue
            try:
                ts = datetime.fromisoformat(timestamp.rstrip("Z")).replace(tzinfo=UTC)
            except ValueError:
                continue
            if ts.timestamp() < cutoff:
                continue
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


def _review_sources(source: str) -> list[tuple[str, str]]:
    if source == _SOURCE_LAUSUNTOPYYNNOT:
        return [(_SOURCE_LAUSUNTOPYYNNOT, "Lausuntopyynnöt")]
    if source == _SOURCE_VALIOKUNTA:
        return [(_SOURCE_VALIOKUNTA, "Valiokunta")]
    if source == "both":
        return [
            (_SOURCE_LAUSUNTOPYYNNOT, "Lausuntopyynnöt"),
            (_SOURCE_VALIOKUNTA, "Valiokunta"),
        ]
    raise ValueError(f"Unknown review source: {source!r}")


def cmd_review_logged(days: int = 7, source: str = _SOURCE_LAUSUNTOPYYNNOT) -> None:
    total = 0
    sections: list[tuple[str, list[dict]]] = []
    for source_key, label in _review_sources(source):
        entries = _read_borderline_entries(days, source=source_key)
        total += len(entries)
        sections.append((label, entries))
    if total == 0:
        print(f"No borderline items in the last {days} days.")
        return

    print(
        f"--- LOGGED ({total} items, score {config.LOG_THRESHOLD}-{config.FLAG_THRESHOLD - 1}) ---\n"
    )
    for label, entries in sections:
        if not entries:
            continue
        if len(sections) > 1:
            print(f"{label}:")
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
    for entry in _read_borderline_entries(days, source=_SOURCE_LAUSUNTOPYYNNOT):
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
    """Print the current lausuntopyyntö digest as plain text."""
    flagged = _load_flagged()
    borderline = _load_borderline(days=days)
    if not flagged and not borderline:
        print("Nothing to preview: no flagged lausuntopyyntö items and no borderline items.")
        return
    subject, _html_body, text_body = build_daily_digest(flagged, borderline)
    print(f"Subject: {subject}\n")
    print(text_body)


def cmd_resend_digest(dry_run: bool, days: int = 7) -> None:
    """Resend the lausuntopyyntö digest without re-running scoring."""
    flagged = _load_flagged()
    borderline = _load_borderline(days=days)
    if not flagged and not borderline:
        print("Nothing to send: no flagged lausuntopyyntö items and no borderline items.")
        return
    _deliver_digest(flagged, dry_run, borderline=borderline)


def _empty_committee_items() -> dict[str, list[dict]]:
    return {key: [] for key in config.COMMITTEE_URLS}


def _build_committee_item(entry: dict) -> dict:
    identifier = entry.get("id")
    eduskuntatunnus = identifier if isinstance(identifier, str) and identifier else "-"
    url = entry.get("url")
    if not isinstance(url, str) or not url:
        url = build_matter_url(eduskuntatunnus if eduskuntatunnus != "-" else "")
    return {
        "title": entry.get("title", ""),
        "eduskuntatunnus": eduskuntatunnus,
        "score": entry.get("score", 0),
        "rationale": entry.get("rationale", ""),
        "themes": entry.get("themes", []),
        "url": url,
    }


def _load_valiokunta_digest(
    days: int = 7,
) -> tuple[dict[str, list[dict]], dict[str, list[dict]], int, int]:
    committee_items = _empty_committee_items()
    borderline_items = _empty_committee_items()
    total_scored = 0
    total_logged = 0

    for entry in _read_recent_score_entries(days, source=_SOURCE_VALIOKUNTA):
        total_scored += 1
        committee_key = entry.get("source")
        if not isinstance(committee_key, str) or not committee_key:
            committee_key = _SOURCE_VALIOKUNTA
        if committee_key not in committee_items:
            committee_items[committee_key] = []
            borderline_items[committee_key] = []

        item = _build_committee_item(entry)
        band = classify_score(item["score"])
        if band == "flag":
            committee_items[committee_key].append(item)
        elif band == "log":
            total_logged += 1
            borderline_items[committee_key].append(item)

    return committee_items, borderline_items, total_scored, total_logged


def _deliver_valiokunta_digest(
    committee_items: dict[str, list[dict]],
    borderline_items: dict[str, list[dict]],
    total_scored: int,
    total_logged: int,
    dry_run: bool,
) -> bool:
    total_flagged = sum(len(items) for items in committee_items.values())
    week_number = datetime.now(UTC).isocalendar().week
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
    print(f"Valiokunta digest sent to {recipient}")
    return True


def cmd_resend_valiokunta_digest(dry_run: bool, days: int = 7) -> None:
    """Resend the valiokunta digest from recent score-log entries."""
    committee_items, borderline_items, total_scored, total_logged = _load_valiokunta_digest(
        days=days
    )
    total_flagged = sum(len(items) for items in committee_items.values())
    if total_flagged == 0 and total_logged == 0:
        print("Nothing to send: no flagged valiokunta items and no borderline items.")
        return
    _deliver_valiokunta_digest(
        committee_items,
        borderline_items,
        total_scored,
        total_logged,
        dry_run,
    )


def cmd_reset_state() -> None:
    print(
        "This will erase all state: seen proposals, seen documents, score logs, and flagged items."
    )
    answer = input("Continue? [y/N] ").strip().lower()
    if answer != "y":
        print("Aborted.")
        return
    _save_json(config.SEEN_PROPOSALS_PATH, {})
    _save_json(config.SEEN_DOCUMENTS_PATH, {})
    config.FLAGGED_PATH.write_text("[]", encoding="utf-8")
    config.LAUSUNTOPALVELU_SCORE_LOG_PATH.write_text("", encoding="utf-8")
    config.VALIOKUNTA_SCORE_LOG_PATH.write_text("", encoding="utf-8")
    config.SCORE_LOG_SPLIT_MIGRATION_MARKER.write_text(
        datetime.now(UTC).isoformat(),
        encoding="utf-8",
    )
    print("State reset.")


def _run_lausuntopyynnot(dry_run: bool) -> None:
    cmd_lausuntopyynnot(dry_run=dry_run)


def _run_valiokunta(dry_run: bool) -> None:
    cmd_valiokunta(dry_run=dry_run)


def _run_review(days: int, source: str) -> None:
    cmd_review_logged(days=days, source=source)


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


def _run_resend_valiokunta(dry_run: bool, days: int | None = None) -> None:
    if days is None:
        cmd_resend_valiokunta_digest(dry_run=dry_run)
    else:
        cmd_resend_valiokunta_digest(dry_run=dry_run, days=days)


def _dispatch_cli(args: argparse.Namespace) -> None:
    actions: list[tuple[str, Callable[[], None]]] = [
        ("update_context", cmd_update_context),
        ("lausuntopyynnot", lambda: _run_lausuntopyynnot(args.dry_run)),
        ("valiokunta", lambda: _run_valiokunta(args.dry_run)),
        ("review_logged", lambda: _run_review(args.days, args.source)),
        ("preview_digest", lambda: _run_preview(args.days)),
        ("resend_digest", lambda: _run_resend(args.dry_run, args.days)),
        ("resend_valiokunta_digest", lambda: _run_resend_valiokunta(args.dry_run, args.days)),
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
            "label": "Lausuntopyyntö check",
            "description": [
                "Fetch new lausuntopalvelu proposals, score with Claude,",
                "and optionally send an email digest.",
            ],
            "action": _menu_lausuntopyynnot,
        },
        {
            "key": "2",
            "label": "Valiokunta check",
            "description": [
                "Fetch new priority committee agendas, score scheduled",
                "matters, and ask before sending the valiokunta digest.",
                "Digest sections are ordered Talousvaliokunta,",
                "Maa- ja metsätalousvaliokunta, Ympäristövaliokunta.",
            ],
            "action": _menu_valiokunta,
        },
        {
            "key": "3",
            "label": "Review borderline items",
            "description": ["Choose source and days to review; default range is 7 days."],
            "action": _menu_review_logged,
        },
        {
            "key": "4",
            "label": "Preview / resend lausuntopyyntö digest",
            "description": [
                "Show the current lausuntopyyntö digest, then ask before",
                "sending. Does not re-run scoring.",
            ],
            "action": lambda: _run_resend(dry_run=False),
        },
        {
            "key": "5",
            "label": "Preview / resend valiokunta digest",
            "description": [
                "Show the valiokunta digest from recent score-log entries,",
                "then ask before sending. Does not re-run scoring.",
            ],
            "action": lambda: _run_resend_valiokunta(dry_run=False),
        },
        {
            "key": "6",
            "label": "Update Kuluttajaliitto context",
            "description": [
                "Re-fetch Kuluttajaliitto published statements used as",
                "scoring context. Checks refresh stale context automatically.",
            ],
            "action": cmd_update_context,
        },
        {
            "key": "r",
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


def _menu_lausuntopyynnot() -> None:
    _run_lausuntopyynnot(dry_run=False)


def _menu_valiokunta() -> None:
    _run_valiokunta(dry_run=False)


def _prompt_review_source() -> str:
    raw = input("Source ([l]ausuntopyynnöt / [v]aliokunta / [b]oth, default l): ").strip().lower()
    if raw in ("", "l", "lausuntopyynnot", "lausuntopyynnöt"):
        return _SOURCE_LAUSUNTOPYYNNOT
    if raw in ("v", "valiokunta"):
        return _SOURCE_VALIOKUNTA
    if raw in ("b", "both", "molemmat"):
        return "both"
    print(f"Invalid source: {raw!r}")
    return ""


def _prompt_review_days() -> int | None:
    raw = input("Days to look back (default 7): ").strip()
    if not raw:
        return 7
    try:
        return int(raw)
    except ValueError:
        print(f"Invalid number: {raw!r}")
        return None


def _menu_review_logged() -> None:
    source = _prompt_review_source()
    if not source:
        return
    days = _prompt_review_days()
    if days is None:
        return
    _run_review(days=days, source=source)


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
        try:
            action()
        # Keep the interactive menu alive when a selected command fails.
        except Exception as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
        print(menu_text)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    _load_runtime_env()

    parser = argparse.ArgumentParser(description="Lausuntobotti – Kuluttajaliitto monitoring tool")
    parser.add_argument(
        "--lausuntopyynnot",
        action="store_true",
        help="Run lausuntopyyntö check",
    )
    parser.add_argument(
        "--valiokunta",
        action="store_true",
        help="Run valiokunta agenda check",
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
        "--source",
        choices=(_SOURCE_LAUSUNTOPYYNNOT, _SOURCE_VALIOKUNTA, "both"),
        default=_SOURCE_LAUSUNTOPYYNNOT,
        help="Score log source for --review-logged (default: lausuntopyynnot)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Days to look back for review and digest preview/resend commands (default: 7)",
    )
    parser.add_argument(
        "--preview-digest",
        action="store_true",
        help="Print current lausuntopyyntö digest as plain text, no email",
    )
    parser.add_argument(
        "--resend-digest",
        action="store_true",
        help="Send lausuntopyyntö digest email without re-running scoring",
    )
    parser.add_argument(
        "--resend-valiokunta-digest",
        action="store_true",
        help="Send valiokunta digest email from recent score log without re-running scoring",
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
            args.lausuntopyynnot,
            args.valiokunta,
            args.update_context,
            args.review_logged,
            args.preview_digest,
            args.resend_digest,
            args.resend_valiokunta_digest,
            args.reset_state,
            args.interactive,
        ]
    ):
        cmd_interactive()
        return

    _dispatch_cli(args)


if __name__ == "__main__":
    main()
