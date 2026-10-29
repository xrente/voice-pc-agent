"""
Streamlit-фронтенд голосового LLM-агента.

Шаг 5: запись с микрофона прямо в браузере через st.audio_input.
Полный цикл: запись → ASR → LLM → TTS → воспроизведение.
"""

import base64
import os

import requests
import streamlit as st


API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")

st.set_page_config(
    page_title="Голосовой агент ПК",
    page_icon="🎙️",
    layout="centered",
)

st.title("🎙️ Голосовой LLM-агент для ПК")
st.caption("Шаг 5 — запись с микрофона + полный голосовой цикл")


# --- Утилиты ---------------------------------------------------------------

def check_health() -> tuple[dict | None, str | None]:
    try:
        r = requests.get(f"{API_URL}/health", timeout=5)
        r.raise_for_status()
        return r.json(), None
    except requests.exceptions.ConnectionError:
        return None, (
            "Не удалось подключиться к API. "
            "Убедитесь, что запущен `python -m uvicorn api:app`."
        )
    except requests.exceptions.Timeout:
        return None, "Превышено время ожидания ответа от API."
    except requests.exceptions.HTTPError as exc:
        return None, f"API вернул ошибку HTTP: {exc}"
    except Exception as exc:
        return None, f"Неизвестная ошибка: {exc}"


def call_process(file_bytes: bytes, filename: str, mime: str,
                 confirmed: bool = False, use_llm: bool = True,
                 llm_model: str = "qwen2.5:7b") -> dict | str:
    try:
        files = {"file": (filename, file_bytes, mime or "application/octet-stream")}
        data = {
            "confirmed": str(confirmed).lower(),
            "use_llm": str(use_llm).lower(),
            "llm_model": llm_model,
        }
        r = requests.post(
            f"{API_URL}/process", files=files, data=data, timeout=600,
        )
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as exc:
        return f"HTTP {exc.response.status_code}: {exc.response.text}"
    except Exception as exc:
        return f"Ошибка соединения: {exc}"


def call_command(text: str, confirmed: bool = False,
                 use_llm: bool = True, llm_model: str = "qwen2.5:7b") -> dict | str:
    try:
        r = requests.post(
            f"{API_URL}/command",
            json={
                "text": text, "confirmed": confirmed,
                "use_llm": use_llm, "llm_model": llm_model,
            },
            timeout=60,
        )
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as exc:
        return f"HTTP {exc.response.status_code}: {exc.response.text}"
    except Exception as exc:
        return f"Ошибка соединения: {exc}"


def render_command_result(result: dict) -> None:
    status = result.get("status")
    if status == "executed":
        st.success(f"✅ Выполнено: `{result['tool']}`")
        st.json(result.get("result"))
    elif status == "needs_confirmation":
        st.warning(f"⚠️ {result.get('message', 'Требуется подтверждение')}")
        st.code(f"{result['tool']}({result['args']})", language="python")
    elif status == "forbidden":
        st.error(f"🚫 {result.get('message', 'Запрещено')}")
    elif status == "unrecognized":
        st.info(f"❔ {result.get('message', 'Не распознано')}")
    elif status == "error":
        st.error(f"💥 {result.get('message', 'Ошибка')}")
    else:
        st.json(result)


def play_base64_audio(audio_b64: str) -> None:
    try:
        wav = base64.b64decode(audio_b64)
        st.audio(wav, format="audio/wav")
    except Exception as exc:
        st.warning(f"Не удалось проиграть аудио: {exc}")


def render_process_result(result: dict) -> None:
    status = result.get("command_status")
    st.markdown(f"**Распознано:** {result.get('recognized_text') or '_(пусто)_'}")
    st.markdown(f"**Ответ агента:** {result.get('response_text')}")
    st.caption(
        f"Источник: `{result.get('source')}` · "
        f"модель: `{result.get('model')}` · "
        f"статус: `{status}` · "
        f"инструмент: `{result.get('tool')}`"
    )

    audio_b64 = result.get("audio_base64")
    if audio_b64:
        st.markdown("**Озвучка ответа:**")
        play_base64_audio(audio_b64)
    else:
        st.info("TTS недоступен — озвучка не получена.")

    if result.get("result"):
        with st.expander("Результат инструмента"):
            st.json(result["result"])


# --- Сайдбар ---------------------------------------------------------------

with st.sidebar:
    st.subheader("Настройки")
    st.write(f"**Адрес API:** `{API_URL}`")
    if st.button("🔄 Обновить статус", use_container_width=True):
        st.rerun()

    st.divider()
    st.subheader("Планировщик")
    use_llm = st.checkbox("Использовать LLM (Ollama)", value=True)
    llm_model = st.selectbox(
        "Модель",
        ["qwen2.5:7b", "qwen2.5:3b", "t-tech/T-lite-it-2.1"],
        index=0, disabled=not use_llm,
    )
    st.caption("При недоступности LLM включается regex-fallback.")

# --- Health ----------------------------------------------------------------

data, error = check_health()
if error:
    st.error(error)
else:
    if data.get("status") == "ok":
        tts_status = "✅" if data.get("tts_loaded") else "⚠️"
        st.success(
            f"✅ API версии `{data['version']}` · TTS {tts_status} · "
            f"время {data['timestamp']}"
        )
    else:
        st.warning(f"API ответил, но статус не «ok»: {data}")

st.divider()

# --- 🎤 Запись с микрофона -------------------------------------------------

st.subheader("🎤 Запись с микрофона")
st.caption(
    "Нажмите на иконку микрофона, произнесите команду и остановите запись. "
    "Затем нажмите «Обработать». Браузер запишет WAV — он декодируется без ffmpeg."
)

audio_value = st.audio_input(
    "Голосовая команда",
    key="mic_input",
)

if audio_value is not None:
    st.audio(audio_value)
    if st.button("🎬 Обработать запись", type="primary", key="mic_process_btn"):
        with st.spinner("ASR → LLM → TTS. Первый раз может быть 30+ секунд..."):
            resp = call_process(
                audio_value.getvalue(),
                audio_value.name or "mic.wav",
                audio_value.type or "audio/wav",
                confirmed=False,
                use_llm=use_llm,
                llm_model=llm_model,
            )
        if isinstance(resp, str):
            st.error(resp)
        else:
            st.session_state["mic_result"] = resp
            st.session_state["mic_bytes"] = audio_value.getvalue()
            st.session_state["mic_name"] = audio_value.name or "mic.wav"
            st.session_state["mic_mime"] = audio_value.type or "audio/wav"

mic_result = st.session_state.get("mic_result")
if mic_result:
    render_process_result(mic_result)

    if mic_result.get("command_status") == "needs_confirmation":
        if st.button("✅ Подтвердить и выполнить", type="primary", key="mic_confirm_btn"):
            with st.spinner("Выполняю..."):
                resp = call_process(
                    st.session_state["mic_bytes"],
                    st.session_state["mic_name"],
                    st.session_state["mic_mime"],
                    confirmed=True,
                    use_llm=use_llm,
                    llm_model=llm_model,
                )
            if isinstance(resp, str):
                st.error(resp)
            else:
                st.session_state["mic_result"] = resp
                st.rerun()

st.divider()

# --- 🎯 Полный цикл из файла -----------------------------------------------

st.subheader("🎯 Полный цикл из файла")
st.caption("Альтернатива микрофону — загрузить готовый аудиофайл.")

voice_file = st.file_uploader(
    "Аудио-команда",
    type=["wav", "mp3", "ogg", "flac", "m4a"],
    accept_multiple_files=False,
    key="voice_uploader",
)

if voice_file is not None:
    st.audio(voice_file)
    if st.button("🎬 Обработать", type="primary", key="file_process_btn"):
        with st.spinner("Обрабатываю..."):
            resp = call_process(
                voice_file.getvalue(),
                voice_file.name,
                voice_file.type or "application/octet-stream",
                confirmed=False,
                use_llm=use_llm,
                llm_model=llm_model,
            )
        if isinstance(resp, str):
            st.error(resp)
        else:
            st.session_state["process_result"] = resp
            st.session_state["process_file_bytes"] = voice_file.getvalue()
            st.session_state["process_file_name"] = voice_file.name
            st.session_state["process_file_mime"] = voice_file.type or "application/octet-stream"

process_result = st.session_state.get("process_result")
if process_result:
    render_process_result(process_result)
    if process_result.get("command_status") == "needs_confirmation":
        if st.button("✅ Подтвердить и выполнить", type="primary", key="file_confirm_btn"):
            with st.spinner("Выполняю..."):
                resp = call_process(
                    st.session_state["process_file_bytes"],
                    st.session_state["process_file_name"],
                    st.session_state["process_file_mime"],
                    confirmed=True,
                    use_llm=use_llm,
                    llm_model=llm_model,
                )
            if isinstance(resp, str):
                st.error(resp)
            else:
                st.session_state["process_result"] = resp
                st.rerun()

st.divider()

# --- 🗣️ Только распознавание -----------------------------------------------

st.subheader("🗣️ Только распознавание (Whisper)")
uploaded = st.file_uploader(
    "Аудио-файл",
    type=["wav", "mp3", "ogg", "flac", "m4a"],
    accept_multiple_files=False,
    key="asr_uploader",
)

if uploaded is not None:
    st.audio(uploaded)
    if st.button("🎤 Распознать", type="primary", key="asr_btn"):
        with st.spinner("Отправляю в Whisper..."):
            try:
                files = {
                    "file": (
                        uploaded.name, uploaded.getvalue(),
                        uploaded.type or "application/octet-stream",
                    )
                }
                r = requests.post(f"{API_URL}/transcribe", files=files, timeout=300)
                r.raise_for_status()
                st.session_state["asr_result"] = r.json()
            except Exception as exc:
                st.error(f"Ошибка: {exc}")

asr = st.session_state.get("asr_result")
if asr:
    st.success("Распознано")
    st.code(asr["text"] or "(пусто)", language=None)
    st.caption(
        f"Язык: `{asr['language']}` · длительность: {asr['duration']:.2f} с · "
        f"модель: `{asr['model']}`"
    )

st.divider()

# --- ⚙️ Текстовые команды --------------------------------------------------

st.subheader("⚙️ Выполнение команды (текстом)")
st.caption("Файловые операции ограничены папкой `workspace/`.")

command_text = st.text_input(
    "Текст команды",
    placeholder="например: можешь открыть ютуб?",
    key="command_input",
)

col1, col2 = st.columns([1, 1])
with col1:
    run_btn = st.button("▶️ Выполнить", type="primary", use_container_width=True)
with col2:
    clear_btn = st.button("🗑️ Очистить результат", use_container_width=True)

if clear_btn:
    st.session_state.pop("command_result", None)

if run_btn and command_text.strip():
    response = call_command(
        command_text, confirmed=False,
        use_llm=use_llm, llm_model=llm_model,
    )
    if isinstance(response, str):
        st.error(response)
    else:
        st.session_state["command_result"] = response

result = st.session_state.get("command_result")
if result:
    render_command_result(result)
    if result.get("status") == "needs_confirmation":
        if st.button("✅ Подтвердить и выполнить", type="primary", key="cmd_confirm_btn"):
            response = call_command(
                result["recognized_text"], confirmed=True,
                use_llm=use_llm, llm_model=llm_model,
            )
            if isinstance(response, str):
                st.error(response)
            else:
                st.session_state["command_result"] = response
                st.rerun()