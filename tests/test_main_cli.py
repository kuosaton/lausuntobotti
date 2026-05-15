from __future__ import annotations

import pytest

import main

_DISPATCHABLE_COMMANDS = (
    "cmd_update_context",
    "cmd_lausuntopyynnot",
    "cmd_valiokunta",
    "cmd_review_logged",
    "cmd_preview_digest",
    "cmd_resend_digest",
    "cmd_resend_valiokunta_digest",
    "cmd_reset_state",
)


def _stub_dispatchable(monkeypatch) -> None:
    for name in _DISPATCHABLE_COMMANDS:
        monkeypatch.setattr(main, name, lambda *args, **kwargs: None)


def _menu_input(inputs: list[str]):
    it = iter(inputs)

    def _fake(_prompt):
        return next(it)

    return _fake


def test_load_runtime_env_skips_dotenv_under_pytest(monkeypatch) -> None:
    called = {"load": False}
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test")
    monkeypatch.setattr(main, "load_dotenv", lambda: called.__setitem__("load", True))

    main._load_runtime_env()

    assert called["load"] is False


def test_load_runtime_env_loads_dotenv_outside_pytest(monkeypatch) -> None:
    called = {"load": False}
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(main, "load_dotenv", lambda: called.__setitem__("load", True))

    main._load_runtime_env()

    assert called["load"] is True


@pytest.mark.parametrize(
    "argv, cmd_attr, expected_call",
    [
        (["--update-context"], "cmd_update_context", ()),
        (["--reset-state"], "cmd_reset_state", ()),
        (["--interactive"], "cmd_interactive", ()),
        (["--lausuntopyynnot"], "cmd_lausuntopyynnot", (False,)),
        (["--lausuntopyynnot", "--dry-run"], "cmd_lausuntopyynnot", (True,)),
        (["--valiokunta"], "cmd_valiokunta", (False,)),
        (["--valiokunta", "--dry-run"], "cmd_valiokunta", (True,)),
        (["--resend-digest"], "cmd_resend_digest", (False, 7)),
        (["--resend-digest", "--dry-run", "--days", "14"], "cmd_resend_digest", (True, 14)),
        (["--resend-valiokunta-digest"], "cmd_resend_valiokunta_digest", (False, 7)),
        (
            ["--resend-valiokunta-digest", "--dry-run", "--days", "14"],
            "cmd_resend_valiokunta_digest",
            (True, 14),
        ),
        (["--review-logged"], "cmd_review_logged", (7, "lausuntopyynnot")),
        (
            ["--review-logged", "--source", "valiokunta", "--days", "14"],
            "cmd_review_logged",
            (14, "valiokunta"),
        ),
        (["--preview-digest"], "cmd_preview_digest", (7,)),
        (["--preview-digest", "--days", "3"], "cmd_preview_digest", (3,)),
    ],
)
def test_main_dispatches_cli_flag(monkeypatch, argv, cmd_attr, expected_call) -> None:
    _stub_dispatchable(monkeypatch)
    captured: dict = {"args": None}

    def _capture(*args, **kwargs):
        captured["args"] = args + tuple(kwargs.values())

    monkeypatch.setattr(main, cmd_attr, _capture)
    monkeypatch.setattr("sys.argv", ["main.py", *argv])

    main.main()

    assert captured["args"] == expected_call


def test_main_rejects_removed_schedule_flags(monkeypatch) -> None:
    _stub_dispatchable(monkeypatch)
    monkeypatch.setattr("sys.argv", ["main.py", "--daily"])

    with pytest.raises(SystemExit) as exc:
        main.main()

    assert exc.value.code == 2


def test_main_dispatches_all_selected_flags(monkeypatch) -> None:
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
            "--lausuntopyynnot",
            "--valiokunta",
            "--review-logged",
            "--source",
            "both",
            "--days",
            "3",
            "--preview-digest",
            "--resend-digest",
            "--resend-valiokunta-digest",
            "--dry-run",
        ],
    )

    main.main()

    assert "cmd_update_context" in called
    assert called["cmd_lausuntopyynnot"][1] == {"dry_run": True}
    assert called["cmd_valiokunta"][1] == {"dry_run": True}
    assert called["cmd_review_logged"][1] == {"days": 3, "source": "both"}
    assert called["cmd_preview_digest"][1] == {"days": 3}
    assert called["cmd_resend_digest"][1] == {"dry_run": True, "days": 3}
    assert called["cmd_resend_valiokunta_digest"][1] == {"dry_run": True, "days": 3}


def test_main_without_flags_launches_interactive(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["main.py"])
    main.main()


@pytest.mark.parametrize(
    "menu_inputs, cmd_attr, expected_call",
    [
        (["1", "0"], "cmd_lausuntopyynnot", (False,)),
        (["2", "0"], "cmd_valiokunta", (False,)),
        (["4", "0"], "cmd_resend_digest", (False,)),
        (["5", "0"], "cmd_resend_valiokunta_digest", (False,)),
        (["6", "0"], "cmd_update_context", ()),
        (["r", "0"], "cmd_reset_state", ()),
    ],
)
def test_interactive_menu_dispatches_choice(
    monkeypatch, menu_inputs, cmd_attr, expected_call
) -> None:
    _stub_dispatchable(monkeypatch)
    captured: dict = {"args": None}

    def _capture(*args, **kwargs):
        captured["args"] = args + tuple(kwargs.values())

    monkeypatch.setattr(main, cmd_attr, _capture)
    monkeypatch.setattr("builtins.input", _menu_input(menu_inputs))
    monkeypatch.setattr("sys.argv", ["main.py"])

    main.main()

    assert captured["args"] == expected_call


def test_interactive_menu_review_defaults_to_lausuntopyynnot_and_7_days(monkeypatch) -> None:
    _stub_dispatchable(monkeypatch)
    captured: dict = {}
    monkeypatch.setattr(
        main,
        "cmd_review_logged",
        lambda days, source: captured.update({"days": days, "source": source}),
    )
    monkeypatch.setattr("builtins.input", _menu_input(["3", "", "", "0"]))
    monkeypatch.setattr("sys.argv", ["main.py"])

    main.main()

    assert captured == {"days": 7, "source": "lausuntopyynnot"}


def test_interactive_menu_review_accepts_custom_source_and_days(monkeypatch) -> None:
    _stub_dispatchable(monkeypatch)
    captured: dict = {}
    monkeypatch.setattr(
        main,
        "cmd_review_logged",
        lambda days, source: captured.update({"days": days, "source": source}),
    )
    monkeypatch.setattr("builtins.input", _menu_input(["3", "v", "14", "0"]))
    monkeypatch.setattr("sys.argv", ["main.py"])

    main.main()

    assert captured == {"days": 14, "source": "valiokunta"}


def test_interactive_menu_invalid_choice(monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", _menu_input(["99", "0"]))
    monkeypatch.setattr("sys.argv", ["main.py"])
    main.main()


def test_interactive_menu_reports_action_error_and_continues(monkeypatch, capsys) -> None:
    _stub_dispatchable(monkeypatch)

    def _raise():
        raise RuntimeError("boom")

    monkeypatch.setattr(main, "cmd_update_context", _raise)
    monkeypatch.setattr("builtins.input", _menu_input(["6", "0"]))
    monkeypatch.setattr("sys.argv", ["main.py"])

    main.main()

    captured = capsys.readouterr()
    assert "[ERROR] boom" in captured.err
    assert captured.out.count("Lausuntobotti") == 2


def test_cmd_interactive_handles_keyboard_interrupt(monkeypatch) -> None:
    def _raise(_prompt):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", _raise)
    main.cmd_interactive()
