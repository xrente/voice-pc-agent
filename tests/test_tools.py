"""
Тесты инструментов.

Браузер и shell мокаются (иначе тесты открывали бы окна и запускали
реальные процессы). Файловые операции выполняются реально, но внутри
временной tmp_workspace (фикстура из conftest.py).
"""

import subprocess
from unittest.mock import patch

import pytest

import tools


# --- Браузер ---------------------------------------------------------------

def test_browser_open_adds_https_scheme():
    with patch("tools.webbrowser.open") as mock_open:
        result = tools.browser_open("example.com")
    mock_open.assert_called_once_with("https://example.com")
    assert result["url"] == "https://example.com"


def test_browser_open_keeps_existing_scheme():
    with patch("tools.webbrowser.open") as mock_open:
        tools.browser_open("https://ya.ru")
    mock_open.assert_called_once_with("https://ya.ru")


def test_youtube_search_encodes_query():
    with patch("tools.webbrowser.open"):
        result = tools.youtube_search("python обучение")
    # URL-энкодинг русских символов
    assert "search_query=" in result["url"]
    assert "%D0%BE" in result["url"]  # «о» в URL-encoded


# --- Файловая система ------------------------------------------------------

def test_filesystem_create_dir(tmp_workspace):
    result = tools.filesystem_create_dir("отчёты")
    assert (tmp_workspace / "отчёты").is_dir()
    assert result["status"] == "ok"


def test_filesystem_create_dir_nested(tmp_workspace):
    tools.filesystem_create_dir("a/b/c")
    assert (tmp_workspace / "a" / "b" / "c").is_dir()


def test_filesystem_create_file(tmp_workspace):
    tools.filesystem_create_file("hello.txt", "привет")
    content = (tmp_workspace / "hello.txt").read_text(encoding="utf-8")
    assert content == "привет"


def test_filesystem_list_dir(tmp_workspace):
    (tmp_workspace / "a.txt").write_text("x", encoding="utf-8")
    (tmp_workspace / "sub").mkdir()
    result = tools.filesystem_list_dir(".")
    assert "a.txt" in result["items"]
    assert "sub/" in result["items"]
    assert result["count"] == 2


def test_filesystem_delete_file(tmp_workspace):
    (tmp_workspace / "x.txt").write_text("x", encoding="utf-8")
    tools.filesystem_delete("x.txt")
    assert not (tmp_workspace / "x.txt").exists()


def test_filesystem_delete_dir_recursive(tmp_workspace):
    sub = tmp_workspace / "sub"
    sub.mkdir()
    (sub / "x.txt").write_text("x", encoding="utf-8")
    tools.filesystem_delete("sub")
    assert not sub.exists()


def test_filesystem_sandbox_escape_blocked(tmp_workspace):
    with pytest.raises(ValueError, match="вне sandbox"):
        tools.filesystem_create_dir("../../evil")


# --- Shell -----------------------------------------------------------------

def test_shell_run_safe_uses_cp866_and_returns_stdout(tmp_workspace):
    fake = subprocess.CompletedProcess(args="dir", returncode=0, stdout="ok\n", stderr="")
    with patch("tools.subprocess.run", return_value=fake) as mock_run:
        result = tools.shell_run_safe("dir")

    assert result["status"] == "ok"
    assert result["stdout"] == "ok\n"
    # Проверяем, что мы действительно передали encoding="cp866" —
    # это фикс «кракозябр» из реальной Windows
    _, kwargs = mock_run.call_args
    assert kwargs.get("encoding") == "cp866"
    assert kwargs.get("errors") == "replace"


def test_shell_run_safe_rejects_forbidden():
    with pytest.raises(PermissionError):
        tools.shell_run_safe("rm -rf /")


def test_shell_run_safe_rejects_confirm():
    """echo с аргументом — CONFIRM; shell_run_safe такой не выполняет."""
    with pytest.raises(PermissionError):
        tools.shell_run_safe("echo hi")