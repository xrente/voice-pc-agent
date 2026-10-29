"""
Общие фикстуры для тестов.

tmp_workspace — подменяет sandbox и лог на временные пути (для модульных тестов).
client_with_mocks — TestClient с замоканными Whisper и TTS (для тестов API).
"""

import io

import numpy as np
import pytest
import soundfile as sf

import safety
import tools
import tts


# --- Изоляция sandbox для модульных тестов ---------------------------------

@pytest.fixture
def tmp_workspace(tmp_path, monkeypatch):
    """Подменяет safety.WORKSPACE и safety.LOG_PATH на временные пути."""
    ws = (tmp_path / "workspace").resolve()
    ws.mkdir()
    monkeypatch.setattr(safety, "WORKSPACE", ws)
    monkeypatch.setattr(safety, "LOG_PATH", tmp_path / "action_log.jsonl")
    return ws


# --- Заглушки моделей ------------------------------------------------------

class _FakeSegment:
    def __init__(self, text: str):
        self.text = text


class _FakeInfo:
    def __init__(self, language: str = "ru", duration: float = 1.0):
        self.language = language
        self.duration = duration


class FakeWhisperModel:
    """Мок faster_whisper.WhisperModel: всегда «слышит» одну и ту же команду."""

    spoken_text = "открой youtube"

    def __init__(self, *args, **kwargs):
        pass

    def transcribe(self, audio, beam_size=5, language=None):
        return [_FakeSegment(self.spoken_text)], _FakeInfo()


class FakeTTSModel:
    """Заглушка Silero TTS — без torch, без apply_tts."""


# --- Генератор валидного WAV для тестов ASR --------------------------------

def make_wav_bytes(duration: float = 0.5, sr: int = 16000) -> bytes:
    samples = np.zeros(int(duration * sr), dtype=np.float32)
    buf = io.BytesIO()
    sf.write(buf, samples, sr, format="WAV")
    return buf.getvalue()


@pytest.fixture
def client_with_mocks(tmp_path, monkeypatch):
    """
    TestClient с подменёнными Whisper и TTS.
    Не требует ни Ollama, ни torch (кроме импорта tts — он уже установлен).
    """
    # Изолируем sandbox и лог
    ws = (tmp_path / "workspace").resolve()
    ws.mkdir()
    monkeypatch.setattr(safety, "WORKSPACE", ws)
    monkeypatch.setattr(safety, "LOG_PATH", tmp_path / "action_log.jsonl")

    # Мок Whisper: подменяем класс в модуле faster_whisper
    import faster_whisper
    monkeypatch.setattr(faster_whisper, "WhisperModel", FakeWhisperModel)

    # Мок TTS: не грузим Silero, не трогаем torch
    monkeypatch.setattr(tts, "load_tts_model", lambda: FakeTTSModel())
    monkeypatch.setattr(
        tts, "synthesize",
        lambda model, text, voice="baya", sample_rate=48000: b"RIFF\x00\x00\x00\x00WAVEfake",
    )

    # Браузер: не открываем реальные окна
    monkeypatch.setattr(tools.webbrowser, "open", lambda url: None)

    # Импортируем api уже после моков — важно для lifespan
    from fastapi.testclient import TestClient
    import api

    with TestClient(api.app) as client:
        yield client
        

@pytest.fixture
def set_spoken_text(monkeypatch):
    """
    Возвращает функцию, которая меняет «услышанный» текст в моке Whisper.

    Работает с тем классом, который реально подставлен в faster_whisper.WhisperModel
    (а не с копией из импорта в тестовом файле) — устраняет проблему двойного
    импорта conftest в pytest.
    """
    import faster_whisper

    target_class = faster_whisper.WhisperModel  # фактический класс в игре

    def _set(text: str) -> None:
        monkeypatch.setattr(target_class, "spoken_text", text)

    return _set


@pytest.fixture
def default_spoken_text():
    """
    Возвращает текст, который мок Whisper «слышит» по умолчанию.
    Нужна, чтобы тесты не импортировали FakeWhisperModel напрямую —
    это защищает от проблемы двойного импорта conftest.
    """
    return "открой youtube"