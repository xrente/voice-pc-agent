"""
Модуль синтеза речи на Silero TTS v5_ru.

Особенности (из практики Colab):
- Модель грузится через torch.hub с GitHub один раз, кэшируется
  в ~/.cache/torch/hub/snakers4_silero-models_master/.
- Silero НЕ выговаривает латиницу — нужен preprocess_text() с заменой
  «youtube» → «ютуб» и т.п. Без этого голос произносит кашу.
- Голос baya, частота 48000 Гц — из проверенного Colab-прототипа.
"""

import io
import re

import soundfile as sf
import torch


# --- Словарь замен латиницы на кириллицу -----------------------------------
# Расширяй по мере необходимости. Ключи — в нижнем регистре,
# регистр в тексте не важен (см. preprocess_text).

REPLACEMENTS: dict[str, str] = {
    # Бренды и сервисы
    "youtube": "ютуб",
    "google": "гугл",
    "gmail": "джимэйл",
    "telegram": "телеграм",
    "whatsapp": "вотсап",
    "github": "гитхаб",
    "windows": "виндоус",
    "linux": "линукс",
    "macos": "макос",
    "android": "андроид",
    "chrome": "хром",
    "firefox": "файрфокс",
    # Технологии
    "python": "питон",
    "javascript": "джаваскрипт",
    "api": "а пи ай",
    "llm": "эл эл эм",
    "ai": "а и",
    "it": "ай ти",
    "cpu": "си пи ю",
    "gpu": "джи пи ю",
    "ram": "оперативная память",
    "url": "урл",
    "http": "эйч ти ти пи",
    "https": "эйч ти ти пи эс",
    "html": "эйч ти эм эл",
    "css": "си эс эс",
    "json": "джейсон",
    "wav": "вав",
    "mp3": "эм пи три",
    # Прочее
    "ok": "окей",
    "pc": "пи си",
    "vs code": "ви эс код",
    "vscode": "ви эс код",
}

# Паттерн для поиска латинских «слов» (включая цифры и подчёркивания)
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def preprocess_text(text: str) -> str:
    """
    Заменяет латиницу на кириллицу, чтобы Silero TTS выговаривал слова.

    1. Сначала ищет фразы из словаря (самые длинные — раньше).
    2. Оставшуюся латиницу выговаривает по буквам через дефисы
       (на случай, если слова нет в словаре).
    """
    # Фразы из нескольких слов (например, "vs code") — раньше одиночных
    for latin, cyr in sorted(REPLACEMENTS.items(), key=lambda x: -len(x[0])):
        text = re.sub(rf"\b{re.escape(latin)}\b", cyr, text, flags=re.IGNORECASE)

    # Оставшиеся латинские токены — «произносим по буквам»
    def _spell(match: re.Match) -> str:
        word = match.group(0)
        return " ".join(word.upper())

    text = _LATIN_WORD.sub(_spell, text)
    return text


# --- Загрузка модели -------------------------------------------------------

def load_tts_model():
    """
    Загружает Silero TTS v5_ru через torch.hub.
    Первый запуск скачает ~50 МБ модели с GitHub (кэшируется).
    """
    model, _ = torch.hub.load(
        repo_or_dir="snakers4/silero-models",
        model="silero_tts",
        language="ru",
        speaker="v5_ru",
        trust_repo=True,  # для новых версий torch
    )
    model.to(torch.device("cpu"))
    return model


def synthesize(model, text: str, voice: str = "baya", sample_rate: int = 48000) -> bytes:
    """
    Синтезирует речь и возвращает WAV-байты.

    text — исходный текст (обработаем preprocess_text сами).
    voice — один из: aidar, baya, kseniya, xenia, eugene, random.
    sample_rate — 48000 для v5_ru.
    """
    cleaned = preprocess_text(text.strip())
    if not cleaned:
        cleaned = " "  # silero падает на пустой строке

    audio = model.apply_tts(text=cleaned, speaker=voice, sample_rate=sample_rate)

    # audio — torch.Tensor, конвертируем в numpy и пишем WAV
    buf = io.BytesIO()
    sf.write(buf, audio.numpy(), sample_rate, format="WAV")
    return buf.getvalue()