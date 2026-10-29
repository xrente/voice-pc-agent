"""
Тесты модуля безопасности.

Это «формальное доказательство» работоспособности механизмов:
- три уровня опасности;
- sandbox (выход за workspace запрещён);
- белый список shell;
- защита от инъекций;
- журналирование.
"""

import json

import pytest

import safety
from safety import DangerLevel


# --- Уровни опасности ------------------------------------------------------

def test_danger_level_values():
    assert DangerLevel.SAFE.value == "safe"
    assert DangerLevel.CONFIRM.value == "confirm"
    assert DangerLevel.FORBIDDEN.value == "forbidden"


# --- Sandbox ---------------------------------------------------------------

def test_resolve_in_workspace_ok(tmp_workspace):
    path = safety.resolve_in_workspace("hello.txt")
    assert path == tmp_workspace / "hello.txt"


def test_resolve_in_workspace_nested(tmp_workspace):
    path = safety.resolve_in_workspace("a/b/c")
    assert path == tmp_workspace / "a" / "b" / "c"


@pytest.mark.parametrize("root_alias", ["", "   ", "."])
def test_resolve_in_workspace_root_aliases(tmp_workspace, root_alias):
    """
    "" / "   " / "." семантически означают «текущая папка» = корень workspace.
    Это НЕ побег из sandbox, поэтому должно возвращать WORKSPACE.
    """
    assert safety.resolve_in_workspace(root_alias) == tmp_workspace


@pytest.mark.parametrize(
    "bad",
    ["..", "../", "../../etc/passwd", "..\\..\\windows"],
)
def test_resolve_in_workspace_rejects_traversal(tmp_workspace, bad):
    with pytest.raises(ValueError, match="вне sandbox"):
        safety.resolve_in_workspace(bad)


def test_resolve_in_workspace_rejects_non_string(tmp_workspace):
    with pytest.raises(ValueError, match="должен быть строкой"):
        safety.resolve_in_workspace(None)  # type: ignore[arg-type]


def test_resolve_in_workspace_rejects_absolute_outside(tmp_workspace, tmp_path):
    # Путь заведомо вне workspace — родитель tmp_path
    outside = str(tmp_path.parent)
    with pytest.raises(ValueError, match="вне sandbox"):
        safety.resolve_in_workspace(outside)


# --- Белый список shell ----------------------------------------------------

@pytest.mark.parametrize("cmd", ["dir", "ls", "pwd", "whoami", "date", "echo"])
def test_shell_whitelist_safe(cmd):
    level, reason = safety.check_shell_command(cmd)
    assert level == DangerLevel.SAFE
    assert reason == ""


@pytest.mark.parametrize(
    "cmd",
    ["shutdown /s", "format c:", "nmap localhost", "regedit", "taskkill /f /im x.exe"],
)
def test_shell_not_in_whitelist_forbidden(cmd):
    level, _ = safety.check_shell_command(cmd)
    assert level == DangerLevel.FORBIDDEN


def test_shell_with_args_confirm():
    level, reason = safety.check_shell_command("echo привет")
    assert level == DangerLevel.CONFIRM
    assert "подтверждения" in reason


def test_shell_empty_forbidden():
    level, _ = safety.check_shell_command("")
    assert level == DangerLevel.FORBIDDEN


@pytest.mark.parametrize(
    "cmd",
    [
        "dir && del *",            # цепочка команд
        "rm -rf /",                 # rm
        "dir | more",               # pipe
        "echo `whoami`",            # backtick
        "echo $(whoami)",           # subshell
        "dir > out.txt",            # redirect
        "cd ../.. && dir",          # traversal через шелл
        "powershell -c whoami",     # powershell явно
    ],
)
def test_shell_injection_forbidden(cmd):
    level, reason = safety.check_shell_command(cmd)
    assert level == DangerLevel.FORBIDDEN
    assert reason  # причина непустая


# --- Журналирование --------------------------------------------------------

def test_log_action_writes_jsonl(tmp_workspace, tmp_path):
    safety.log_action({"event": "test", "payload": 42})
    log_path = tmp_path / "action_log.jsonl"
    assert log_path.exists()
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event"] == "test"
    assert record["payload"] == 42
    assert "timestamp" in record