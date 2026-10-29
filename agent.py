"""
Планировщик действий агента.

Два режима:
1. rule-based (plan) — fallback, работает всегда, без интернета.
2. LLM-based (plan_llm) — локальная модель через Ollama (Qwen 2.5),
   понимает свободные формулировки и поддерживает tool calling.
"""

import json
import re
from dataclasses import dataclass

import tools
from safety import DangerLevel


@dataclass
class Plan:
    """План выполнения команды."""

    tool: str
    args: dict
    danger: DangerLevel
    reason: str = ""
    description: str = ""


TOOLS: dict[str, callable] = {
    "browser.open": tools.browser_open,
    "youtube.search": tools.youtube_search,
    "filesystem.create_dir": tools.filesystem_create_dir,
    "filesystem.create_file": tools.filesystem_create_file,
    "filesystem.list_dir": tools.filesystem_list_dir,
    "filesystem.delete": tools.filesystem_delete,
    "shell.run_safe": tools.shell_run_safe,
}


# --- Rule-based планировщик (fallback) -------------------------------------

def plan(text: str) -> Plan | None:
    """Regex-планировщик. Работает без интернета и LLM."""
    t = text.strip().lower()

    m = re.match(r"^выполни\s+(.+)$", t)
    if m:
        cmd = m.group(1).strip()
        from safety import check_shell_command
        level, reason = check_shell_command(cmd)
        return Plan(
            tool="shell.run_safe",
            args={"command": cmd},
            danger=level,
            reason=reason,
            description=f"выполнить shell-команду: {cmd}",
        )

    m = re.match(r"^открой\s+(.+)$", t)
    if m:
        target = m.group(1).strip()
        if target in {"ютуб", "youtube", "ютюб"}:
            return Plan(
                tool="browser.open",
                args={"url": "https://www.youtube.com"},
                danger=DangerLevel.SAFE,
                description="открыть YouTube",
            )
        return Plan(
            tool="browser.open",
            args={"url": target},
            danger=DangerLevel.SAFE,
            description=f"открыть {target}",
        )

    m = re.match(r"^(?:найди|поищи|загугли)\s+(?:на\s+ютубе\s+|в\s+ютубе\s+)?(.+)$", t)
    if m:
        query = m.group(1).strip()
        return Plan(
            tool="youtube.search",
            args={"query": query},
            danger=DangerLevel.SAFE,
            description=f"найти на YouTube: {query}",
        )

    m = re.match(r"^создай\s+папку\s+(.+)$", t)
    if m:
        name = m.group(1).strip()
        return Plan(
            tool="filesystem.create_dir",
            args={"name": name},
            danger=DangerLevel.SAFE,
            description=f"создать папку {name}",
        )

    m = re.match(r"^создай\s+файл\s+(.+)$", t)
    if m:
        name = m.group(1).strip()
        return Plan(
            tool="filesystem.create_file",
            args={"name": name, "content": ""},
            danger=DangerLevel.SAFE,
            description=f"создать файл {name}",
        )

    m = re.match(r"^(?:покажи|список)\s+(?:содержимое\s+)?(?:папки\s+)?(.+)$", t)
    if m:
        name = m.group(1).strip()
        return Plan(
            tool="filesystem.list_dir",
            args={"name": name},
            danger=DangerLevel.SAFE,
            description=f"показать содержимое {name}",
        )

    m = re.match(r"^удали\s+(.+)$", t)
    if m:
        name = m.group(1).strip()
        return Plan(
            tool="filesystem.delete",
            args={"name": name},
            danger=DangerLevel.CONFIRM,
            reason="удаление файлов необратимо",
            description=f"УДАЛИТЬ {name}",
        )

    return None


# --- LLM-based планировщик (Ollama + Qwen 2.5) ------------------------------

_ollama_client = None


def _get_ollama_client():
    """Ленивая инициализация клиента Ollama."""
    global _ollama_client
    if _ollama_client is not None:
        return _ollama_client

    import ollama
    _ollama_client = ollama.Client()
    return _ollama_client


# Схема инструментов для tool calling в формате Ollama.
_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "browser_open",
            "description": "Открыть URL в браузере по умолчанию",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL или домен"}
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "youtube_search",
            "description": "Открыть поиск YouTube по запросу",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "поисковый запрос"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "filesystem_create_dir",
            "description": "Создать папку внутри workspace",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "имя папки"}
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "filesystem_create_file",
            "description": "Создать файл внутри workspace",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "имя файла"},
                    "content": {"type": "string", "description": "содержимое (можно пустое)"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "filesystem_list_dir",
            "description": "Показать содержимое папки внутри workspace",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "имя папки; по умолчанию '.'"}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "filesystem_delete",
            "description": "Удалить файл или папку внутри workspace (опасно, требует подтверждения)",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "имя файла или папки"}
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_run_safe",
            "description": "Выполнить безопасную shell-команду из белого списка",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "shell-команда"}
                },
                "required": ["command"],
            },
        },
    },
]

_FUNCTION_TO_TOOL = {
    "browser_open": "browser.open",
    "youtube_search": "youtube.search",
    "filesystem_create_dir": "filesystem.create_dir",
    "filesystem_create_file": "filesystem.create_file",
    "filesystem_list_dir": "filesystem.list_dir",
    "filesystem_delete": "filesystem.delete",
    "shell_run_safe": "shell.run_safe",
}


def plan_llm(text: str, model_name: str = "qwen2.5:7b") -> Plan | None:
    """
    Планировщик на локальной LLM через Ollama.
    Поддерживает tool calling для Qwen 2.5 и совместимых моделей.
    """
    client = _get_ollama_client()

    try:
        response = client.chat(
            model=model_name,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты — планировщик команд для голосового ассистента ПК. "
                        "На основе запроса пользователя выбери ОДНУ функцию "
                        "из списка и сгенерируй для неё аргументы. "
                        "Если запрос не относится ни к одной функции — не вызывай ничего."
                    ),
                },
                {"role": "user", "content": text},
            ],
            tools=_TOOLS_SCHEMA,
        )
    except Exception as exc:
        raise RuntimeError(f"Ошибка при вызове Ollama: {exc}") from exc

    message = response["message"]
    tool_calls = message.get("tool_calls")
    if not tool_calls:
        return None

    tool_call = tool_calls[0]
    func_name = tool_call["function"]["name"]
    tool_name = _FUNCTION_TO_TOOL.get(func_name)
    if tool_name is None:
        return None

    args = tool_call["function"].get("arguments", {})
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return None

    if tool_name == "shell.run_safe":
        from safety import check_shell_command
        level, reason = check_shell_command(args.get("command", ""))
    elif tool_name == "filesystem.delete":
        level, reason = DangerLevel.CONFIRM, "удаление файлов необратимо"
    else:
        level, reason = DangerLevel.SAFE, ""

    return Plan(
        tool=tool_name,
        args=args,
        danger=level,
        reason=reason,
        description=f"LLM ({model_name}): {func_name}({args})",
    )


def execute(plan_obj: Plan) -> dict:
    """Выполняет план. Бросает исключение, если инструмент неизвестен."""
    fn = TOOLS.get(plan_obj.tool)
    if fn is None:
        raise ValueError(f"неизвестный инструмент: {plan_obj.tool}")
    return fn(**plan_obj.args)