"""
Тесты FastAPI-эндпоинтов с замоканными Whisper и TTS.

Проверяют:
- /health, /commands;
- /transcribe (пустой файл, валидный WAV);
- /synthesize;
- /command во всех статусах (executed, forbidden, needs_confirmation, unrecognized);
- /process (полный цикл) и подтверждение CONFIRM-команды.
"""

from tests.conftest import make_wav_bytes


# --- Служебные -------------------------------------------------------------

def test_health(client_with_mocks):
    r = client_with_mocks.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["tts_loaded"] is True
    assert data["version"] == "0.4.0"


def test_commands_list(client_with_mocks):
    r = client_with_mocks.get("/commands")
    assert r.status_code == 200
    data = r.json()
    assert "commands" in data
    assert len(data["commands"]) >= 5


# --- /transcribe -----------------------------------------------------------

def test_transcribe_ok(client_with_mocks, default_spoken_text):
    """Валидный WAV → Whisper возвращает текст из мока."""
    wav = make_wav_bytes()
    r = client_with_mocks.post(
        "/transcribe",
        files={"file": ("test.wav", wav, "audio/wav")},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["text"] == default_spoken_text
    assert data["language"] == "ru"
    assert data["model"]  # имя модели непустое


def test_transcribe_empty_file(client_with_mocks):
    r = client_with_mocks.post(
        "/transcribe",
        files={"file": ("empty.wav", b"", "audio/wav")},
    )
    assert r.status_code == 400


# --- /synthesize -----------------------------------------------------------

def test_synthesize_ok(client_with_mocks):
    r = client_with_mocks.post(
        "/synthesize",
        json={"text": "привет мир", "voice": "baya"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["audio_base64"]
    assert data["voice"] == "baya"
    assert data["text"] == "привет мир"


# --- /command --------------------------------------------------------------

def test_command_safe(client_with_mocks):
    r = client_with_mocks.post(
        "/command",
        json={"text": "создай папку тесты", "use_llm": False},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "executed"
    assert data["tool"] == "filesystem.create_dir"


def test_command_forbidden(client_with_mocks):
    r = client_with_mocks.post(
        "/command",
        json={"text": "выполни rm -rf /", "use_llm": False},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "forbidden"
    assert data["danger"] == "forbidden"


def test_command_needs_confirmation(client_with_mocks):
    r = client_with_mocks.post(
        "/command",
        json={"text": "удали мусор", "use_llm": False},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "needs_confirmation"
    assert data["danger"] == "confirm"


def test_command_confirmed_executes(client_with_mocks):
    # Сначала создаём папку, чтобы было что удалять
    client_with_mocks.post(
        "/command",
        json={"text": "создай папку мусор", "use_llm": False},
    )
    # Затем подтверждённое удаление
    r = client_with_mocks.post(
        "/command",
        json={"text": "удали мусор", "confirmed": True, "use_llm": False},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "executed"
    assert data["tool"] == "filesystem.delete"


def test_command_unrecognized(client_with_mocks):
    r = client_with_mocks.post(
        "/command",
        json={"text": "абракадабра", "use_llm": False},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "unrecognized"


# --- /process --------------------------------------------------------------

def test_process_full_cycle(client_with_mocks, default_spoken_text):
    """Полный цикл: аудио → ASR → regex-план → выполнение → TTS."""
    wav = make_wav_bytes()
    r = client_with_mocks.post(
        "/process",
        files={"file": ("cmd.wav", wav, "audio/wav")},
        data={"use_llm": "false", "llm_model": "qwen2.5:7b"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["recognized_text"] == default_spoken_text
    assert data["command_status"] == "executed"
    assert data["tool"] == "browser.open"
    assert data["audio_base64"]  # TTS отработал
    assert data["source"] == "regex"
    assert data["model"] == "regex"


def test_process_confirm_cycle(client_with_mocks, set_spoken_text):
    """CONFIRM-команда по аудио: без confirmed → needs_confirmation, с confirmed → executed."""
    # Меняем «услышанный» текст через фикстуру — надёжно даже при двойном импорте
    set_spoken_text("удали мусор")

    # Подготовим объект для удаления
    client_with_mocks.post(
        "/command",
        json={"text": "создай папку мусор", "use_llm": False},
    )

    wav = make_wav_bytes()

    # Без confirmed
    r1 = client_with_mocks.post(
        "/process",
        files={"file": ("cmd.wav", wav, "audio/wav")},
        data={"use_llm": "false"},
    )
    assert r1.status_code == 200
    data1 = r1.json()
    assert data1["command_status"] == "needs_confirmation"
    assert data1["tool"] == "filesystem.delete"

    # С confirmed
    r2 = client_with_mocks.post(
        "/process",
        files={"file": ("cmd.wav", wav, "audio/wav")},
        data={"use_llm": "false", "confirmed": "true"},
    )
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["command_status"] == "executed"