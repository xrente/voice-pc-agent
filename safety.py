"""
Модуль безопасности голосового агента.

Здесь сосредоточены ВСЕ правила, ограничивающие действия агента:
- уровни опасности команд (SAFE / CONFIRM / FORBIDDEN);
- белый список shell-команд;
- sandbox для файловых операций (всё только внутри workspace/);
- журналирование выполненных действий.

Идея: инструменты (tools.py) НЕ принимают решений о безопасности —
они делегируют это сюда. Так одна точка контроля, один набор тестов,
одна секция в диссертации.
"""

import json
from datetime import datetime
from enum import Enum
from pathlib import Path


# --- Уровни опасности ------------------------------------------------------

class DangerLevel(str, Enum):
    """
    Три уровня, как в требованиях к проекту.
    str-наследование, чтобы значение сериализовалось в JSON как строка.
    """

    SAFE = "safe"           # выполняем молча
    CONFIRM = "confirm"     # требуем явного подтверждения пользователя
    FORBIDDEN = "forbidden" # отклоняем всегда


# --- Sandbox для файловых операций -----------------------------------------

# Корень sandbox: всё, что делает агент с файлами, живёт здесь.
# resolve() важен: сравниваем абсолютные пути, чтобы не обмануться
# через "..", симлинки и относительные пути.
WORKSPACE: Path = (Path(__file__).parent / "workspace").resolve()
WORKSPACE.mkdir(exist_ok=True)


def resolve_in_workspace(rel: str) -> Path:
    """
    Приводит относительный путь к абсолютному внутри WORKSPACE
    и проверяет, что он не выходит за пределы sandbox.

    Семантика:
    - "" , "   " и "." трактуются как корень workspace (WORKSPACE).
      Это легитимная ссылка «на текущую папку» — не побег из sandbox,
      поэтому допустима. Без неё filesystem_list_dir(name=".")
      не смог бы работать.
    - Путь, который после resolve() оказался вне WORKSPACE (например,
      "../etc", "../../..", абсолютный путь снаружи), — ValueError.
      Это FORBIDDEN-случай для файловых инструментов.

    Бросает ValueError при попытке выйти наружу.
    """
    if not isinstance(rel, str):
        raise ValueError(f"путь должен быть строкой, получено: {type(rel).__name__}")

    normalized = rel.strip()

    # Пустая строка / точка = «текущая папка» = корень workspace
    if normalized in ("", "."):
        return WORKSPACE

    path = (WORKSPACE / normalized).resolve()

    # path должен быть либо самим WORKSPACE, либо внутри него
    if path != WORKSPACE and WORKSPACE not in path.parents:
        raise ValueError(f"путь вне sandbox: {rel} → {path}")
    return path


# --- Белый список shell-команд ---------------------------------------------

# Только эти команды разрешены. Всё остальное — FORBIDDEN по умолчанию.
SHELL_WHITELIST: set[str] = {
    "dir", "ls", "pwd", "whoami", "date", "time",
    "hostname", "ver", "echo",
}

# Даже если команда в белом списке, наличие этих подстрок = FORBIDDEN.
# Защита от инъекций вида `echo rm -rf /` или `dir && del *`.
SHELL_FORBIDDEN_FRAGMENTS: list[str] = [
    "rm ", "del ", "rmdir ", "format ", "shutdown", "restart",
    "reg ", "net user", "powershell", "pwsh", "cmd /c", "sudo ",
    "chmod ", "chown ", "> ", ">>", " | ", " && ", " || ", "; ",
    "`", "$(", "..\\", "../",
]


def check_shell_command(command: str) -> tuple[DangerLevel, str]:
    """
    Проверяет shell-команду по белому списку и запрещённым фрагментам.

    Возвращает (уровень опасности, причина).
    Для SAFE причина — пустая строка.
    """
    stripped = command.strip()
    if not stripped:
        return DangerLevel.FORBIDDEN, "пустая команда"

    lower = stripped.lower()

    # 1. Сначала ищем запрещённые фрагменты — они опаснее всего
    for frag in SHELL_FORBIDDEN_FRAGMENTS:
        if frag in lower:
            return DangerLevel.FORBIDDEN, f"содержит запрещённый фрагмент {frag!r}"

    # 2. Разбиваем на имя и аргументы
    parts = stripped.split()
    name = parts[0].lower()

    # 3. Имя должно быть в белом списке
    if name not in SHELL_WHITELIST:
        return DangerLevel.FORBIDDEN, f"команда {name!r} не в белом списке"

    # 4. Команда с аргументами — потенциально опаснее, чем без них
    if len(parts) > 1:
        return DangerLevel.CONFIRM, (
            f"команда {name!r} с аргументами требует подтверждения"
        )

    return DangerLevel.SAFE, ""


# --- Журналирование --------------------------------------------------------

LOG_PATH: Path = Path(__file__).parent / "action_log.jsonl"


def log_action(entry: dict) -> None:
    """
    Пишет одну запись в журнал действий (JSONL).
    Каждая строка — самостоятельный JSON-объект: удобно грепать и грузить
    в pandas без парсинга файла целиком.
    """
    record = {"timestamp": datetime.now().isoformat(timespec="seconds"), **entry}
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")