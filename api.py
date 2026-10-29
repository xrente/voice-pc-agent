"""
FastAPI-бэкенд голосового LLM-агента для управления ПК.

Шаг 4: полный голосовой цикл.
ASR (Whisper) → LLM (Ollama) / regex → TTS (Silero v5_ru, baya).
"""

import base64
import io
import os
from contextlib import asynccontextmanager
from datetime import datetime

import librosa
import numpy as np
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

import agent
import tts
from safety import DangerLevel, WORKSPACE, log_action


# --- Конфигурация ----------------------------------------------------------

WHISPER_MODEL_NAME = os.getenv("WHISPER_MODEL", "small")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
TTS_VOICE = os.getenv("TTS_VOICE", "baya")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Загрузка Whisper и Silero TTS один раз при старте."""
    from faster_whisper import WhisperModel

    print(
        f"[startup] Загружаю Whisper: {WHISPER_MODEL_NAME} "
        f"(device={WHISPER_DEVICE}, compute={WHISPER_COMPUTE_TYPE})"
    )
    app.state.whisper_model = WhisperModel(
        WHISPER_MODEL_NAME,
        device=WHISPER_DEVICE,
        compute_type=WHISPER_COMPUTE_TYPE,
    )
    print("[startup] Whisper готов")

    # TTS опционален: если torch/silero не установились — работаем без TTS
    print(f"[startup] Загружаю Silero TTS (voice={TTS_VOICE})...")
    try:
        app.state.tts_model = tts.load_tts_model()
        print("[startup] Silero TTS готов")
    except Exception as exc:
        print(f"[startup] WARN: TTS не загрузился ({exc}). /synthesize и /process "
              f"будут работать без озвучки.")
        app.state.tts_model = None

    print(f"[startup] Sandbox: {WORKSPACE}")
    yield
    print("[shutdown] Останавливаем сервер")


app = FastAPI(
    title="Voice PC Agent API",
    description="Голосовой LLM-агент для управления персональным компьютером",
    version="0.4.0",
    lifespan=lifespan,
)


# --- Схемы ------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    timestamp: str
    tts_loaded: bool


class TranscribeResponse(BaseModel):
    text: str
    language: str | None
    duration: float
    model: str


class SynthesizeRequest(BaseModel):
    text: str
    voice: str = TTS_VOICE


class SynthesizeResponse(BaseModel):
    text: str
    audio_base64: str
    voice: str


class CommandRequest(BaseModel):
    text: str
    confirmed: bool = False
    use_llm: bool = True
    llm_model: str = "qwen2.5:7b"


class CommandResponse(BaseModel):
    recognized_text: str
    tool: str | None
    args: dict | None
    danger: str | None
    status: str
    message: str | None = None
    result: dict | None = None


class ProcessResponse(BaseModel):
    recognized_text: str
    response_text: str
    audio_base64: str | None
    command_status: str | None
    tool: str | None
    danger: str | None
    result: dict | None = None
    source: str | None = None
    model: str | None = None


# --- Вспомогательные функции ----------------------------------------------

def _decode_audio(raw: bytes, sr: int = 16000) -> np.ndarray:
    """
    Декодирует произвольный аудио-контейнер в моно float32 с частотой sr.

    Два бэкенда — graceful degradation:
    1. librosa/soundfile: wav, mp3, ogg, flac.
    2. pydub + static-ffmpeg: m4a, aac, opus, webm и всё остальное,
       что умеет ffmpeg. static-ffmpeg сам скачивает ffmpeg и ffprobe
       и прописывает их в PATH.
    """
    if not raw:
        raise HTTPException(status_code=400, detail="Пустой файл")

    # Бэкенд 1: librosa
    try:
        audio, _ = librosa.load(io.BytesIO(raw), sr=sr, mono=True)
        if audio.size > 0:
            return audio.astype(np.float32)
    except Exception as exc:
        print(f"[decode] librosa не справилась ({exc}), пробую ffmpeg")

    # Бэкенд 2: pydub + static-ffmpeg (ffmpeg + ffprobe в PATH)
    try:
        import static_ffmpeg
        static_ffmpeg.add_paths()  # скачивает бинарники (один раз) и кладёт в PATH

        from pydub import AudioSegment

        seg = AudioSegment.from_file(io.BytesIO(raw))
        seg = seg.set_frame_rate(sr).set_channels(1)

        samples = np.array(seg.get_array_of_samples(), dtype=np.float32)
        if seg.sample_width == 2:
            samples /= 32768.0
        elif seg.sample_width == 4:
            samples /= 2147483648.0
        elif seg.sample_width == 1:
            samples = (samples - 128.0) / 128.0
        return samples.astype(np.float32)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Не удалось декодировать аудио: {exc}",
        ) from exc


def _transcribe_bytes(raw: bytes) -> tuple[str, str | None, float]:
    """Декодирует аудио и прогоняет через Whisper."""
    audio = _decode_audio(raw)
    if audio.size == 0:
        raise HTTPException(status_code=400, detail="Аудио пустое после декодирования")

    segments, info = app.state.whisper_model.transcribe(audio, beam_size=5, language=None)
    text = " ".join(seg.text.strip() for seg in segments if seg.text.strip())
    return text, info.language, float(info.duration)


def _plan_with_fallback(text: str, use_llm: bool, llm_model: str) -> tuple[agent.Plan | None, str]:
    """LLM → fallback на regex."""
    if use_llm:
        try:
            plan_obj = agent.plan_llm(text, model_name=llm_model)
            if plan_obj is not None:
                return plan_obj, "llm"
        except Exception as exc:
            print(f"[llm] fallback на regex: {exc}")
    return agent.plan(text), "regex"


def _run_plan(plan_obj: agent.Plan | None, text: str, confirmed: bool, source: str) -> CommandResponse:
    """
    Выполняет план с учётом уровней опасности.
    Возвращает CommandResponse (тот же формат, что /command).
    """
    if plan_obj is None:
        log_action({"event": "unrecognized", "text": text, "source": source})
        return CommandResponse(
            recognized_text=text, tool=None, args=None, danger=None,
            status="unrecognized",
            message=(
                "Команда не распознана. Примеры: «открой YouTube», "
                "«создай папку отчёты», «покажи workspace», «выполни dir»."
            ),
        )

    if plan_obj.danger == DangerLevel.FORBIDDEN:
        log_action({
            "event": "forbidden", "text": text, "tool": plan_obj.tool,
            "reason": plan_obj.reason, "source": source,
        })
        return CommandResponse(
            recognized_text=text, tool=plan_obj.tool, args=plan_obj.args,
            danger=plan_obj.danger.value, status="forbidden",
            message=f"Команда запрещена: {plan_obj.reason}",
        )

    if plan_obj.danger == DangerLevel.CONFIRM and not confirmed:
        log_action({
            "event": "needs_confirmation", "text": text, "tool": plan_obj.tool,
            "reason": plan_obj.reason, "source": source,
        })
        return CommandResponse(
            recognized_text=text, tool=plan_obj.tool, args=plan_obj.args,
            danger=plan_obj.danger.value, status="needs_confirmation",
            message=(
                f"Требуется подтверждение: {plan_obj.reason}. "
                f"Действие: {plan_obj.description}."
            ),
        )

    try:
        result = agent.execute(plan_obj)
    except Exception as exc:
        log_action({
            "event": "error", "text": text, "tool": plan_obj.tool,
            "error": str(exc), "source": source,
        })
        return CommandResponse(
            recognized_text=text, tool=plan_obj.tool, args=plan_obj.args,
            danger=plan_obj.danger.value, status="error",
            message=f"Ошибка выполнения: {exc}",
        )

    log_action({
        "event": "executed", "text": text, "tool": plan_obj.tool,
        "args": plan_obj.args, "danger": plan_obj.danger.value,
        "source": source, "result": result,
    })
    return CommandResponse(
        recognized_text=text, tool=plan_obj.tool, args=plan_obj.args,
        danger=plan_obj.danger.value, status="executed", result=result,
    )


def _formulate_response(resp: CommandResponse) -> str:
    """Превращает CommandResponse в естественную фразу для озвучки."""
    if resp.status == "executed" and resp.result:
        tool = resp.tool
        args = resp.args or {}
        r = resp.result
        if tool == "browser.open":
            return f"Открываю {args.get('url', 'сайт')}"
        if tool == "youtube.search":
            return f"Ищу на ютубе: {args.get('query', '')}"
        if tool == "filesystem.create_dir":
            return f"Создала папку {args.get('name', '')}"
        if tool == "filesystem.create_file":
            return f"Создала файл {args.get('name', '')}"
        if tool == "filesystem.list_dir":
            return f"В папке {r.get('count', 0)} элементов"
        if tool == "filesystem.delete":
            return f"Удалила {args.get('name', '')}"
        if tool == "shell.run_safe":
            return "Команда выполнена"
        return "Готово"

    if resp.status == "needs_confirmation":
        return resp.message or "Требуется подтверждение"
    if resp.status == "forbidden":
        return resp.message or "Команда запрещена"
    if resp.status == "unrecognized":
        return "Извините, не поняла команду. Попробуйте переформулировать"
    if resp.status == "error":
        return resp.message or "Произошла ошибка"
    return "Готово"


def _tts_bytes(text: str) -> str | None:
    """Синтезирует и возвращает base64-WAV, либо None при ошибке."""
    if app.state.tts_model is None:
        return None
    try:
        wav = tts.synthesize(app.state.tts_model, text, voice=TTS_VOICE)
        return base64.b64encode(wav).decode("ascii")
    except Exception as exc:
        print(f"[tts] ошибка синтеза: {exc}")
        return None


# --- Эндпоинты --------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["Служебные"])
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="voice-pc-agent-api",
        version=app.version,
        timestamp=datetime.now().isoformat(timespec="seconds"),
        tts_loaded=app.state.tts_model is not None,
    )


@app.post("/transcribe", response_model=TranscribeResponse, tags=["ASR"])
async def transcribe(file: UploadFile = File(...)) -> TranscribeResponse:
    raw = await file.read()
    text, lang, duration = _transcribe_bytes(raw)
    return TranscribeResponse(
        text=text, language=lang, duration=duration, model=WHISPER_MODEL_NAME,
    )


@app.post("/synthesize", response_model=SynthesizeResponse, tags=["TTS"])
def synthesize_endpoint(req: SynthesizeRequest) -> SynthesizeResponse:
    """Синтез речи: текст → WAV (base64)."""
    if app.state.tts_model is None:
        raise HTTPException(status_code=503, detail="TTS не загружен")
    try:
        wav = tts.synthesize(app.state.tts_model, req.text, voice=req.voice)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка синтеза: {exc}") from exc
    return SynthesizeResponse(
        text=req.text,
        audio_base64=base64.b64encode(wav).decode("ascii"),
        voice=req.voice,
    )


@app.get("/commands", tags=["Агент"])
def list_commands() -> dict:
    return {
        "commands": [
            {"pattern": "открой <url|youtube|ютуб>", "tool": "browser.open", "danger": "safe"},
            {"pattern": "найди <запрос>", "tool": "youtube.search", "danger": "safe"},
            {"pattern": "создай папку <имя>", "tool": "filesystem.create_dir", "danger": "safe"},
            {"pattern": "создай файл <имя>", "tool": "filesystem.create_file", "danger": "safe"},
            {"pattern": "покажи <путь>", "tool": "filesystem.list_dir", "danger": "safe"},
            {"pattern": "удали <путь>", "tool": "filesystem.delete", "danger": "confirm"},
            {"pattern": "выполни <shell-команда>", "tool": "shell.run_safe", "danger": "safe|confirm|forbidden"},
        ]
    }


@app.post("/command", response_model=CommandResponse, tags=["Агент"])
def command(req: CommandRequest) -> CommandResponse:
    plan_obj, source = _plan_with_fallback(req.text, req.use_llm, req.llm_model)
    return _run_plan(plan_obj, req.text, req.confirmed, source)


@app.post("/process", response_model=ProcessResponse, tags=["Полный цикл"])
async def process(
    file: UploadFile = File(...),
    confirmed: bool = Form(False),
    use_llm: bool = Form(True),
    llm_model: str = Form("qwen2.5:7b"),
) -> ProcessResponse:
    """
    Полный голосовой цикл: аудио → ASR → план → выполнение → TTS.
    Возвращает распознанный текст, ответ агента и аудио ответа (base64 WAV).
    """
    raw = await file.read()
    recognized, _, _ = _transcribe_bytes(raw)

    if not recognized.strip():
        return ProcessResponse(
            recognized_text="",
            response_text="Не расслышала команду. Попробуйте ещё раз",
            audio_base64=_tts_bytes("Не расслышала команду. Попробуйте ещё раз"),
            command_status="unrecognized",
            tool=None,
            danger=None,
            source=None,
            model=llm_model if use_llm else "regex",
        )

    plan_obj, source = _plan_with_fallback(recognized, use_llm, llm_model)
    cmd_resp = _run_plan(plan_obj, recognized, confirmed, source)
    response_text = _formulate_response(cmd_resp)
    audio_b64 = _tts_bytes(response_text)

    return ProcessResponse(
        recognized_text=recognized,
        response_text=response_text,
        audio_base64=audio_b64,
        command_status=cmd_resp.status,
        tool=cmd_resp.tool,
        danger=cmd_resp.danger,
        result=cmd_resp.result,
        source=source,
        model=llm_model if use_llm else "regex",
    )


if __name__ == "__main__":
    uvicorn.run("api:app", host="127.0.0.1", port=8000, reload=False)