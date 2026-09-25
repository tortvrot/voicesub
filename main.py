# -*- coding: utf-8 -*-
"""
VoiceSub v2 — локальный переводчик голоса в субтитры для онлайн-игр.
Работает на Windows и macOS (захват звука реализован по-разному под каждую ОС).

ЧТО НОВОГО В V2:
- Поток распознавания больше не "умирает молча" при ошибке — если что-то
  пошло не так на одной фразе, программа печатает ошибку и продолжает слушать
  дальше, а не зависает на "Ожидание речи..." навсегда.
- Короче задержки перед обработкой фразы -> перевод появляется быстрее.
- Используются все ядра процессора для распознавания (cpu_threads).
- setup_languages.py теперь ставит языковые пакеты "через английский" —
  так работает перевод с китайского/корейского/японского/испанского и т.д.,
  даже если прямого пакета "язык -> русский" не существует.
- Поддержка macOS через захват звука с виртуального устройства BlackHole.

ПЕРЕД ЗАПУСКОМ:
- Один раз запусти setup_languages.py (нужен интернет).
- На Windows дополнительно ничего ставить не надо (PyAudioWPatch сам всё умеет).
- На Mac нужно один раз установить BlackHole (см. README-mac.md).
"""

import os
import sys
import platform
import queue
import threading
import time
import numpy as np

# ---------- НАСТРОЙКИ (меняй под себя) ----------
SOURCE_LANG = "auto"     # "auto" — определять язык автоматически
TARGET_LANG = "ru"       # На какой язык переводить субтитры
WHISPER_MODEL = "small"  # tiny / base / small / medium
SHOW_ORIGINAL = False    # True — показывать также исходный текст мелким шрифтом

# Настройки задержки — чем меньше, тем быстрее появляется перевод,
# но тем выше риск обрезать фразу на середине. Подбирай под себя.
SILENCE_TIMEOUT = 0.4    # сек тишины, после которых считаем фразу законченной
MAX_PHRASE_SECONDS = 6    # максимальная длина фразы перед принудительным распознаванием
MIN_PHRASE_SECONDS = 0.3  # короче этого — игнорируем как шум/щелчок

# Черновой перевод "на лету", пока человек ещё говорит (не дожидаясь тишины).
# Субтитры выглядят живее, но модель работает чаще -> чуть больше нагрузка на CPU.
ENABLE_PARTIAL_STREAMING = True
PARTIAL_INTERVAL = 0.8       # как часто обновлять черновой перевод, сек
MIN_PARTIAL_SECONDS = 0.6    # не пытаться распознать совсем короткий обрывок фразы

SAMPLE_RATE = 16000       # частота, которую ожидает whisper
SPEECH_ENERGY_THRESHOLD = 0.01  # порог громкости для определения "есть речь"
# --------------------------------------------------

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"

audio_queue = queue.Queue()   # элементы: (float32 numpy mono chunk, sample_rate)
text_queue = queue.Queue()    # элементы: (original_text, translated_text)


# ================= ЗАХВАТ ЗВУКА =================

def audio_capture_windows():
    """Захват системного звука через WASAPI loopback (PyAudioWPatch)."""
    import pyaudiowpatch as pyaudio

    p = pyaudio.PyAudio()

    def get_current_loopback_device():
        """Возвращает актуальное loopback-устройство для текущего устройства
        вывода по умолчанию (пересчитывается каждый раз заново, поэтому сразу
        видит смену наушники <-> колонки)."""
        wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        default_speakers = p.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
        if not default_speakers.get("isLoopbackDevice", False):
            for loopback in p.get_loopback_device_info_generator():
                if default_speakers["name"] in loopback["name"]:
                    return loopback
        return default_speakers

    current_device_name = None
    stream = None

    try:
        while True:
            try:
                device = get_current_loopback_device()
            except OSError:
                print("Не найден WASAPI. На Windows это обязательно должно быть.")
                sys.exit(1)

            # Устройство вывода поменялось (например, подключили наушники) -
            # закрываем старый поток и открываем новый на актуальном устройстве.
            if device["name"] != current_device_name:
                if stream is not None:
                    try:
                        stream.stop_stream()
                        stream.close()
                    except Exception:
                        pass

                current_device_name = device["name"]
                print(f"Слушаю устройство: {current_device_name}")

                channels = int(device["maxInputChannels"])
                rate = int(device["defaultSampleRate"])

                def make_callback(channels=channels, rate=rate):
                    def callback(in_data, frame_count, time_info, status):
                        audio_np = np.frombuffer(in_data, dtype=np.int16).astype(np.float32) / 32768.0
                        if channels > 1:
                            audio_np = audio_np.reshape(-1, channels).mean(axis=1)
                        audio_queue.put((audio_np, rate))
                        return (in_data, pyaudio.paContinue)
                    return callback

                try:
                    stream = p.open(
                        format=pyaudio.paInt16,
                        channels=channels,
                        rate=rate,
                        input=True,
                        input_device_index=device["index"],
                        stream_callback=make_callback(),
                    )
                    stream.start_stream()
                    print("Захват звука запущен. Говори/включай голосовой чат — субтитры появятся сверху.")
                except Exception as e:
                    print(f"[Не удалось открыть устройство, пробую снова через секунду]: {e}")
                    stream = None
                    current_device_name = None

            # Проверяем раз в секунду, не поменялось ли устройство вывода
            time.sleep(1.0)
    finally:
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass


def audio_capture_mac():
    """
    Захват системного звука на macOS через виртуальное устройство BlackHole.
    ВАЖНО: перед запуском нужно установить BlackHole (brew install blackhole-2ch)
    и создать Multi-Output Device в Audio MIDI Setup, чтобы одновременно
    слышать звук самому и отправлять его в программу. См. README-mac.md.
    """
    import sounddevice as sd

    devices = sd.query_devices()
    target_index = None
    for i, d in enumerate(devices):
        if "blackhole" in d["name"].lower() and d["max_input_channels"] > 0:
            target_index = i
            break

    if target_index is None:
        print("Не найдено устройство BlackHole. Установи его (brew install blackhole-2ch)")
        print("и настрой Multi-Output Device в Audio MIDI Setup — см. README-mac.md")
        sys.exit(1)

    dev_info = devices[target_index]
    rate = int(dev_info["default_samplerate"])
    channels = dev_info["max_input_channels"]

    print(f"Слушаю устройство: {dev_info['name']}")

    def callback(indata, frames, time_info, status):
        audio_np = indata.astype(np.float32)
        if channels > 1:
            audio_np = audio_np.mean(axis=1)
        else:
            audio_np = audio_np.flatten()
        audio_queue.put((audio_np, rate))

    with sd.InputStream(
        device=target_index,
        channels=channels,
        samplerate=rate,
        callback=callback,
        dtype="float32",
    ):
        print("Захват звука запущен. Говори/включай голосовой чат — субтитры появятся сверху.")
        while True:
            time.sleep(0.2)


def audio_capture_thread():
    if IS_WINDOWS:
        audio_capture_windows()
    elif IS_MAC:
        audio_capture_mac()
    else:
        print("Эта программа поддерживает только Windows и macOS.")
        sys.exit(1)


# ================= ОБРАБОТКА ЗВУКА =================

def resample_to_16k(audio_float32, orig_rate):
    """Простой ресемплинг (линейная интерполяция) до 16кГц, как ждёт whisper."""
    if orig_rate == SAMPLE_RATE:
        return audio_float32
    duration = len(audio_float32) / orig_rate
    new_len = int(duration * SAMPLE_RATE)
    if new_len <= 0:
        return audio_float32
    return np.interp(
        np.linspace(0, len(audio_float32), new_len, endpoint=False),
        np.arange(len(audio_float32)),
        audio_float32,
    ).astype(np.float32)


def is_speech(audio_chunk):
    if len(audio_chunk) == 0:
        return False
    energy = np.sqrt(np.mean(audio_chunk ** 2))
    return energy > SPEECH_ENERGY_THRESHOLD


def recognizer_thread():
    """
    Собирает аудио в фразы и прогоняет через whisper + переводчик.
    В v2 обёрнуто в try/except на каждой итерации — одна ошибка на одной фразе
    больше не убивает весь поток, программа продолжает слушать дальше.
    """
    from faster_whisper import WhisperModel
    import argostranslate.translate

    print("Загружаю модель распознавания речи (может занять время при первом запуске)...")
    cpu_threads = os.cpu_count() or 4
    model = WhisperModel(
        WHISPER_MODEL, device="cpu", compute_type="int8", cpu_threads=cpu_threads
    )
    print(f"Модель загружена (используется {cpu_threads} потоков CPU).")

    buffer_chunks = []
    last_speech_time = None
    phrase_start_time = None
    last_partial_time = None

    def transcribe_buffer(chunks):
        rate0 = chunks[0][1]
        merged = np.concatenate([resample_to_16k(c, r) for c, r in chunks])
        if len(merged) < SAMPLE_RATE * MIN_PHRASE_SECONDS:
            return None, None
        segments, info = model.transcribe(
            merged,
            language=None if SOURCE_LANG == "auto" else SOURCE_LANG,
            beam_size=1,
            vad_filter=False,
            condition_on_previous_text=False,
            temperature=0.0,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return text or None, info.language if text else None

    def translate_text(text, detected_lang):
        if detected_lang == TARGET_LANG:
            return text
        try:
            return argostranslate.translate.translate(text, detected_lang, TARGET_LANG)
        except Exception:
            return f"[Нет пакета перевода {detected_lang}->{TARGET_LANG}] {text}"

    while True:
        try:
            data, rate = audio_queue.get(timeout=0.2)
        except queue.Empty:
            data = None

        now = time.time()

        if data is not None:
            buffer_chunks.append((data, rate))
            if is_speech(data):
                last_speech_time = now
                if phrase_start_time is None:
                    phrase_start_time = now

        # --- ЧЕРНОВОЙ перевод прямо во время речи (не дожидаясь тишины) ---
        # Это делает субтитры похожими на "живые" - текст обновляется, пока
        # человек ещё говорит, а не появляется одним куском после паузы.
        if (
            ENABLE_PARTIAL_STREAMING
            and buffer_chunks
            and phrase_start_time is not None
            and (now - phrase_start_time) >= MIN_PARTIAL_SECONDS
            and (last_partial_time is None or (now - last_partial_time) >= PARTIAL_INTERVAL)
        ):
            try:
                text, detected_lang = transcribe_buffer(list(buffer_chunks))
                if text:
                    translated = translate_text(text, detected_lang)
                    text_queue.put((text, translated + " …", False))
            except Exception as e:
                print(f"[Черновой перевод не удался, не страшно, жду финальную версию]: {e}")
            last_partial_time = now

        # --- ФИНАЛЬНЫЙ перевод после паузы или слишком длинной фразы ---
        should_process = False
        if buffer_chunks and last_speech_time is not None:
            if now - last_speech_time > SILENCE_TIMEOUT:
                should_process = True
            elif phrase_start_time and (now - phrase_start_time) > MAX_PHRASE_SECONDS:
                should_process = True

        if not should_process:
            continue

        chunks_to_process = buffer_chunks
        buffer_chunks = []
        last_speech_time = None
        phrase_start_time = None
        last_partial_time = None

        try:
            text, detected_lang = transcribe_buffer(chunks_to_process)
            if not text:
                continue
            translated = translate_text(text, detected_lang)
            text_queue.put((text, translated, True))

        except Exception as e:
            # Раньше ошибка тут убивала весь поток молча.
            # Теперь просто печатаем её и продолжаем слушать дальше.
            print(f"[Ошибка при распознавании одной фразы, продолжаю работу]: {e}")
            continue


# ================= ИНТЕРФЕЙС (СУБТИТРЫ) =================

def overlay_ui():
    import tkinter as tk

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.85)
    root.config(bg="black")

    screen_w = root.winfo_screenwidth()
    win_w, win_h = 1000, 150 if SHOW_ORIGINAL else 120
    win_h += 26  # место под верхнюю панель с кнопкой закрытия
    x = (screen_w - win_w) // 2
    y = 40
    root.geometry(f"{win_w}x{win_h}+{x}+{y}")

    def close_app(event=None):
        os._exit(0)  # сразу завершает программу вместе со всеми фоновыми потоками

    top_bar = tk.Frame(root, bg="black")
    top_bar.pack(fill="x")

    close_btn = tk.Label(
        top_bar,
        text="✕ Закрыть",
        font=("Arial", 11, "bold"),
        fg="white",
        bg="#552222",
        cursor="hand2",
        padx=10,
        pady=3,
    )
    close_btn.pack(side="right", padx=4, pady=2)
    close_btn.bind("<Button-1>", close_app)

    label = tk.Label(
        root,
        text="Ожидание речи...",
        font=("Arial", 20, "bold"),
        fg="white",
        bg="black",
        wraplength=win_w - 20,
        justify="center",
    )
    label.pack(expand=True, fill="both")

    original_label = None
    if SHOW_ORIGINAL:
        original_label = tk.Label(
            root,
            text="",
            font=("Arial", 12),
            fg="#aaaaaa",
            bg="black",
            wraplength=win_w - 20,
            justify="center",
        )
        original_label.pack(fill="x")

    def poll_queue():
        try:
            while True:
                original, translated, is_final = text_queue.get_nowait()
                label.config(text=translated)
                if original_label is not None:
                    original_label.config(text=original)
                tag = "финал" if is_final else "черновик"
                print(f"[{tag}] [{original}] -> {translated}")
        except queue.Empty:
            pass
        root.after(100, poll_queue)

    def keep_on_top():
        # Windows иногда "забывает" topmost-флаг у окна без рамки после
        # сворачивания/переключения полноэкранных игр - напоминаем ему
        # каждую секунду, что окно должно быть поверх всего.
        try:
            root.attributes("-topmost", True)
            root.lift()
        except Exception:
            pass
        root.after(1000, keep_on_top)

    root.after(100, poll_queue)
    root.after(1000, keep_on_top)
    root.mainloop()


def main():
    t1 = threading.Thread(target=audio_capture_thread, daemon=True)
    t2 = threading.Thread(target=recognizer_thread, daemon=True)
    t1.start()
    t2.start()
    overlay_ui()


if __name__ == "__main__":
    main()
