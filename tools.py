"""
Реализация инструментов (скиллов) агента.

Каждая функция — это «глагол», который агент умеет выполнять.
Все инструменты:
- принимают простые типы (str), чтобы аргументы легко сериализовались;
- возвращают dict, пригодный для JSON-ответа API;
- НЕ решают вопросов безопасности сами — файловые операции идут через
  safety.resolve_in_workspace (он бросит ValueError, если путь выходит
  за sandbox), shell — через safety.check_shell_command.
"""

import shutil
import subprocess
import webbrowser
from pathlib import Path
from urllib.parse import quote_plus

from safety import DangerLevel, WORKSPACE, check_shell_command, resolve_in_workspace


# --- Браузер ---------------------------------------------------------------

def browser_open(url: str) -> dict:
    """Открывает URL в браузере по умолчанию. Домены https:// добавляем, если нет схемы."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    return {"action": "browser.open", "url": url, "status": "ok"}


def youtube_search(query: str) -> dict:
    """Открывает результаты поиска YouTube по запросу."""
    q = query.strip()
    url = f"https://www.youtube.com/results?search_query={quote_plus(q)}"
    webbrowser.open(url)
    return {"action": "youtube.search", "query": q, "url": url, "status": "ok"}


# --- Файловая система (sandbox) --------------------------------------------

def filesystem_create_dir(name: str) -> dict:
    """Создаёт папку внутри workspace/."""
    path = resolve_in_workspace(name)
    path.mkdir(parents=True, exist_ok=True)
    return {"action": "filesystem.create_dir", "path": str(path), "status": "ok"}


def filesystem_create_file(name: str, content: str = "") -> dict:
    """Создаёт (или перезаписывает) файл внутри workspace/."""
    path = resolve_in_workspace(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {
        "action": "filesystem.create_file",
        "path": str(path),
        "size": len(content),
        "status": "ok",
    }


def filesystem_list_dir(name: str = ".") -> dict:
    """Возвращает содержимое папки внутри workspace/."""
    path = resolve_in_workspace(name)
    if not path.exists():
        raise FileNotFoundError(f"не найдено: {name}")
    if not path.is_dir():
        raise NotADirectoryError(f"не папка: {name}")
    items = sorted(
        f"{p.name}/" if p.is_dir() else p.name for p in path.iterdir()
    )
    return {
        "action": "filesystem.list_dir",
        "path": str(path),
        "items": items,
        "count": len(items),
        "status": "ok",
    }


def filesystem_delete(name: str) -> dict:
    """
    Удаляет файл или папку внутри workspace/.
    ОПАСНО: всегда должно выполняться только после подтверждения
    пользователя (DangerLevel.CONFIRM).
    """
    path = resolve_in_workspace(name)
    if not path.exists():
        raise FileNotFoundError(f"не найдено: {name}")
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return {"action": "filesystem.delete", "path": str(path), "status": "ok"}


# --- Shell (только белый список) -------------------------------------------

def shell_run_safe(command: str) -> dict:
    """
    Запускает shell-команду, только если она прошла проверку
    safety.check_shell_command как SAFE. CONFIRM тоже отвергаем —
    этот инструмент принимает только безопасные команды,
    подтверждение обрабатывается на уровне агента.
    """
    level, reason = check_shell_command(command)
    if level != DangerLevel.SAFE:
        raise PermissionError(f"shell.run_safe отклонил команду: {reason}")

    # Явно задаём кодировку консоли Windows (cp866 на русской локали)
    # и errors="replace", чтобы не падать, если в выводе окажется
    # символ, который нельзя декодировать. Без этого subprocess.run
    # с text=True может выдать «кракозябры» или UnicodeDecodeError.
    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        encoding="cp866",
        errors="replace",
        timeout=10,
        cwd=str(WORKSPACE),
    )
    return {
        "action": "shell.run_safe",
        "command": command,
        "stdout": result.stdout[:4000],
        "stderr": result.stderr[:4000],
        "returncode": result.returncode,
        "status": "ok",
    }