"""
Тесты rule-based планировщика.

Проверяем, что тексты команд корректно превращаются в Plan
и что уровень опасности выставляется правильно.
"""

import pytest

import agent
import safety
from safety import DangerLevel


# --- Браузер / YouTube -----------------------------------------------------

def test_plan_browser_youtube():
    p = agent.plan("открой youtube")
    assert p is not None
    assert p.tool == "browser.open"
    assert p.args == {"url": "https://www.youtube.com"}
    assert p.danger == DangerLevel.SAFE


def test_plan_browser_ютуб():
    p = agent.plan("открой ютуб")
    assert p.tool == "browser.open"
    assert p.args["url"] == "https://www.youtube.com"


def test_plan_browser_arbitrary_url():
    p = agent.plan("открой example.com")
    assert p.tool == "browser.open"
    assert p.args["url"] == "example.com"


def test_plan_youtube_search():
    p = agent.plan("найди python обучение")
    assert p.tool == "youtube.search"
    assert p.args["query"] == "python обучение"


# --- Файловая система ------------------------------------------------------

def test_plan_create_dir():
    p = agent.plan("создай папку отчёты")
    assert p.tool == "filesystem.create_dir"
    assert p.args["name"] == "отчёты"
    assert p.danger == DangerLevel.SAFE


def test_plan_create_file():
    p = agent.plan("создай файл test.txt")
    assert p.tool == "filesystem.create_file"
    assert p.args["name"] == "test.txt"


def test_plan_list_dir():
    p = agent.plan("покажи workspace")
    assert p.tool == "filesystem.list_dir"
    assert p.args["name"] == "workspace"


def test_plan_delete_requires_confirmation():
    p = agent.plan("удали отчёты")
    assert p.tool == "filesystem.delete"
    assert p.danger == DangerLevel.CONFIRM
    assert p.reason  # непустая причина


# --- Shell -----------------------------------------------------------------

def test_plan_shell_safe():
    p = agent.plan("выполни dir")
    assert p.tool == "shell.run_safe"
    assert p.args == {"command": "dir"}
    assert p.danger == DangerLevel.SAFE


def test_plan_shell_confirm():
    p = agent.plan("выполни echo привет")
    assert p.tool == "shell.run_safe"
    assert p.danger == DangerLevel.CONFIRM


def test_plan_shell_forbidden():
    p = agent.plan("выполни rm -rf /")
    assert p.tool == "shell.run_safe"
    assert p.danger == DangerLevel.FORBIDDEN


# --- Негативные сценарии ---------------------------------------------------

def test_plan_unrecognized_returns_none():
    assert agent.plan("абракадабра") is None


def test_plan_priority_shell_before_other():
    """
    «выполни покажи» не должно интерпретироваться как filesystem.list_dir
    (regex «покажи …»). Shell-правило имеет приоритет.
    """
    p = agent.plan("выполни покажи")
    assert p.tool == "shell.run_safe"
    # «покажи» — не в whitelist → FORBIDDEN
    assert p.danger == DangerLevel.FORBIDDEN


# --- execute ---------------------------------------------------------------

def test_execute_unknown_tool_raises():
    bad = agent.Plan(tool="nope", args={}, danger=DangerLevel.SAFE)
    with pytest.raises(ValueError, match="неизвестный инструмент"):
        agent.execute(bad)


def test_execute_dispatches_to_tool(monkeypatch):
    """execute() должен найти инструмент в реестре и вызвать его с args."""
    calls = []

    def fake_browser_open(url):
        calls.append(url)
        return {"action": "browser.open", "url": url, "status": "ok"}

    monkeypatch.setitem(agent.TOOLS, "browser.open", fake_browser_open)

    p = agent.Plan(
        tool="browser.open",
        args={"url": "https://example.com"},
        danger=DangerLevel.SAFE,
    )
    result = agent.execute(p)
    assert calls == ["https://example.com"]
    assert result["status"] == "ok"