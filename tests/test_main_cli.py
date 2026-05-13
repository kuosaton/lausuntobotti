from __future__ import annotations

import pytest

import main

# Commands that argparse dispatches to. cmd_interactive is excluded because some
# tests need the real menu loop to run.
_DISPATCHABLE_COMMANDS = (
    "cmd_update_context",
    "cmd_daily",
    "cmd_weekly",
    "cmd_midweek",
    "cmd_review_logged",
    "cmd_preview_digest",
    "cmd_resend_digest",
    "cmd_reset_state",
)


def _stub_dispatchable(monkeypatch) -> None:
    """No-op every dispatchable command so a test only sees the one under test."""
    for name in _DISPATCHABLE_COMMANDS:
        monkeypatch.setattr(main, name, lambda *args, **kwargs: None)


def _menu_input(inputs: list[str]):
    """Build a mock input() that returns `inputs` in order at the '>' prompt."""
    it = iter(inputs)

    def _fake(prompt):
        if prompt.strip() == ">":
            return next(it)
        return "y"

    return _fake


# ---------------------------------------------------------------------------
# CLI flag dispatch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv, cmd_attr, expected_call",
    [
        # Simple no-arg commands
        (["--update-context"], "cmd_update_context", ()),
        (["--reset-state"], "cmd_reset_state", ()),
        (["--interactive"], "cmd_interactive", ()),
        # Commands that take dry_run — both default (False) and explicit True
        (["--daily"], "cmd_daily", (False,)),
        (["--daily", "--dry-run"], "cmd_daily", (True,)),
        (["--weekly"], "cmd_weekly", (False,)),
        (["--weekly", "--dry-run"], "cmd_weekly", (True,)),
        (["--midweek"], "cmd_midweek", (False,)),
        (["--resend-digest"], "cmd_resend_digest", (False, 7)),
        (["--resend-digest", "--dry-run"], "cmd_resend_digest", (True, 7)),
        (["--resend-digest", "--dry-run", "--days", "14"], "cmd_resend_digest", (True, 14)),
        # Commands that take a days argument — default and explicit
        (["--review-logged"], "cmd_review_logged", (7,)),
        (["--review-logged", "--days", "14"], "cmd_review_logged", (14,)),
        (["--preview-digest"], "cmd_preview_digest", (7,)),
        (["--preview-digest", "--days", "3"], "cmd_preview_digest", (3,)),
    ],
)
def test_main_dispatches_cli_flag(monkeypatch, argv, cmd_attr, expected_call) -> None:
    """Each CLI flag invokes its command with the right argument values."""
    _stub_dispatchable(monkeypatch)

    captured: dict = {"args": None}

    def _capture(*args, **kwargs):
        captured["args"] = args + tuple(kwargs.values())

    monkeypatch.setattr(main, cmd_attr, _capture)
    monkeypatch.setattr("sys.argv", ["main.py", *argv])

    main.main()

    assert captured["args"] == expected_call


def test_main_dispatches_all_selected_flags(monkeypatch) -> None:
    """All flags together: each command is invoked exactly once with right args."""
    called: dict = {}

    def _record(name):
        def _f(*args, **kwargs):
            called[name] = (args, kwargs)

        return _f

    for name in _DISPATCHABLE_COMMANDS:
        monkeypatch.setattr(main, name, _record(name))

    monkeypatch.setattr(
        "sys.argv",
        [
            "main.py",
            "--update-context",
            "--daily",
            "--weekly",
            "--midweek",
            "--review-logged",
            "--days",
            "3",
            "--preview-digest",
            "--resend-digest",
            "--dry-run",
        ],
    )

    main.main()

    assert "cmd_update_context" in called
    assert called["cmd_daily"][1] == {"dry_run": True}  # --dry-run propagates
    assert called["cmd_weekly"][1] == {"dry_run": True}
    assert called["cmd_midweek"][1] == {"dry_run": True}
    assert called["cmd_review_logged"][1] == {"days": 3}
    assert called["cmd_preview_digest"][1] == {"days": 3}
    assert called["cmd_resend_digest"][1] == {"dry_run": True, "days": 3}


def test_main_without_flags_launches_interactive(monkeypatch) -> None:
    """No flags → interactive menu (which auto-exits on EOF)."""
    monkeypatch.setattr("sys.argv", ["main.py"])
    main.main()  # should not raise


# ---------------------------------------------------------------------------
# Interactive menu dispatch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "menu_key, cmd_attr, expected_call",
    [
        ("1", "cmd_daily", (False,)),
        ("2", "cmd_daily", (True,)),
        ("3", "cmd_update_context", ()),
        ("4", "cmd_review_logged", (7,)),
        ("6", "cmd_preview_digest", ()),
        ("7", "cmd_resend_digest", (False,)),
        ("8", "cmd_reset_state", ()),
    ],
)
def test_interactive_menu_dispatches_choice(monkeypatch, menu_key, cmd_attr, expected_call) -> None:
    """Each numeric menu key invokes its command with the right argument values."""
    _stub_dispatchable(monkeypatch)

    captured: dict = {"args": None}

    def _capture(*args, **kwargs):
        captured["args"] = args + tuple(kwargs.values())

    monkeypatch.setattr(main, cmd_attr, _capture)
    monkeypatch.setattr("builtins.input", _menu_input([menu_key, "0"]))
    monkeypatch.setattr("sys.argv", ["main.py"])

    main.main()
    assert captured["args"] == expected_call


def test_interactive_menu_invalid_choice(monkeypatch) -> None:
    """Unknown menu input prints help and continues; '0' exits cleanly."""
    monkeypatch.setattr("builtins.input", _menu_input(["99", "0"]))
    monkeypatch.setattr("sys.argv", ["main.py"])
    main.main()  # should not raise


def test_interactive_menu_choice_review_custom_days_valid(monkeypatch) -> None:
    """Option 5 prompts for days; valid int passes through."""
    _stub_dispatchable(monkeypatch)
    called: dict = {}
    monkeypatch.setattr(main, "cmd_review_logged", lambda days: called.__setitem__("days", days))
    inputs = iter(["5", "14", "0"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    monkeypatch.setattr("sys.argv", ["main.py"])

    main.main()
    assert called["days"] == 14


def test_interactive_menu_choice_review_custom_days_invalid(monkeypatch, capsys) -> None:
    """Option 5 with non-numeric input prints error, does NOT call review."""
    _stub_dispatchable(monkeypatch)
    called: dict = {"count": 0}
    monkeypatch.setattr(
        main, "cmd_review_logged", lambda days: called.__setitem__("count", called["count"] + 1)
    )
    inputs = iter(["5", "oops", "0"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    monkeypatch.setattr("sys.argv", ["main.py"])

    main.main()
    out = capsys.readouterr().out
    assert "Invalid number" in out
    assert called["count"] == 0


def test_cmd_interactive_handles_keyboard_interrupt(monkeypatch) -> None:
    """Ctrl-C at the menu prompt exits cleanly."""

    def _raise(prompt):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", _raise)
    main.cmd_interactive()


# ---------------------------------------------------------------------------
# Unimplemented commands
# ---------------------------------------------------------------------------


def test_unimplemented_commands_exit() -> None:
    """--weekly and --midweek currently exit 1 (planned features)."""
    with pytest.raises(SystemExit) as weekly:
        main.cmd_weekly(dry_run=True)
    with pytest.raises(SystemExit) as midweek:
        main.cmd_midweek(dry_run=True)
    assert weekly.value.code == 1
    assert midweek.value.code == 1
