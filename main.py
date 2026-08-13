import os
import platform
import queue
import sys
import tempfile
import threading
import time

import requests
import sounddevice as sd
import soundfile as sf
import yaml
from pynput import keyboard

IS_WINDOWS = platform.system() == "Windows"

user32 = None
VK_CONTROL = 0x11
VK_SHIFT = 0x10

if IS_WINDOWS:
    import ctypes

    user32 = ctypes.windll.user32

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")

DEFAULT_CONFIG = {
    "server": {
        "url": "http://192.168.1.200:8000/v1/audio/transcriptions",
        "model": "whisper-1",
        "language": "",
        "timeout": 30,
    },
    "shortcuts": {
        "toggle": "<ctrl>+<shift>+i",
        "cancel": "<esc>",
    },
    "audio": {
        "sample_rate": 16000,
        "channels": 1,
    },
    "behavior": {
        "toggle_debounce_seconds": 0.4,
        "main_loop_sleep_seconds": 0.5,
    },
    "ui": {
        "title_prefix": "Whisper-to-Text",
        "title_ready": "🔵 [KLAR]",
        "title_recording": "🔴 [OPPTAK...]",
        "title_processing": "⏳ [BEHANDLER...]",
    },
}


def load_config(path):
    """Leser YAML-konfigurasjon. Faller tilbake til defaults hvis filen mangler."""
    if not os.path.exists(path):
        print(f"⚠️  Fant ikke '{path}'. Bruker innebygde standardverdier.")
        return DEFAULT_CONFIG

    try:
        with open(path, "r", encoding="utf-8") as f:
            user_config = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        print(f"⚠️  Kunne ikke tolke '{path}': {e}. Bruker innebygde standardverdier.")
        return DEFAULT_CONFIG

    merged = {}
    for section, defaults in DEFAULT_CONFIG.items():
        user_section = user_config.get(section, {}) or {}
        merged[section] = {**defaults, **user_section}
    return merged


CONFIG = load_config(CONFIG_PATH)
API_URL = CONFIG["server"]["url"]
MODEL_NAME = CONFIG["server"]["model"]
LANGUAGE = CONFIG["server"]["language"]
API_TIMEOUT = CONFIG["server"]["timeout"]

SAMPLE_RATE = int(CONFIG["audio"]["sample_rate"])
CHANNELS = int(CONFIG["audio"]["channels"])

TOGGLE_DEBOUNCE = float(CONFIG["behavior"]["toggle_debounce_seconds"])
MAIN_LOOP_SLEEP = float(CONFIG["behavior"]["main_loop_sleep_seconds"])

UI_PREFIX = CONFIG["ui"]["title_prefix"]
UI_READY = CONFIG["ui"]["title_ready"]
UI_RECORDING = CONFIG["ui"]["title_recording"]
UI_PROCESSING = CONFIG["ui"]["title_processing"]

VK_A = 0x41
VK_Z = 0x5A
VK_ESCAPE = 0x1B
VK_MAP = {VK_ESCAPE: "escape"}
for vk in range(VK_A, VK_Z + 1):
    VK_MAP[vk] = chr(vk).lower()


def parse_shortcut(spec):
    """Parser '<ctrl>+<shift>+i' -> ({'ctrl', 'shift'}, 'i', VK-kode)."""
    parts = [p.strip().lower() for p in spec.split("+")]
    modifiers = set()
    main_key = None
    for part in parts:
        if part in ("<ctrl>", "ctrl", "control"):
            modifiers.add("ctrl")
        elif part in ("<shift>", "shift"):
            modifiers.add("shift")
        elif part in ("<alt>", "alt"):
            modifiers.add("alt")
        else:
            main_key = part.strip("<>")
    vk_code = None
    if len(main_key) == 1 and main_key.isalpha():
        vk_code = VK_A + (ord(main_key.upper()) - ord("A"))
    elif main_key == "escape" or main_key == "esc":
        vk_code = VK_ESCAPE
    return modifiers, main_key, vk_code


TOGGLE_MODIFIERS, TOGGLE_KEY, TOGGLE_VK = parse_shortcut(CONFIG["shortcuts"]["toggle"])
CANCEL_MODIFIERS, CANCEL_KEY, CANCEL_VK = parse_shortcut(CONFIG["shortcuts"]["cancel"])

is_recording = False
recording_thread = None
audio_queue = queue.Queue()
stream = None
temp_file_path = None
listener = None

last_press_time = 0.0
escape_was_cancelled = False


def set_terminal_title(title):
    if IS_WINDOWS:
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleTitleW(title)
        except Exception:
            pass
    else:
        try:
            sys.stdout.write(f"\x1b]2;{title}\x07")
            sys.stdout.flush()
        except Exception:
            pass


def audio_callback(indata, frames, time_info, status):
    if status:
        print(status, file=sys.stderr)
    audio_queue.put(indata.copy())


def start_recording():
    global is_recording, stream, temp_file_path, audio_queue
    is_recording = True
    audio_queue = queue.Queue()

    set_terminal_title(f"{UI_RECORDING} {UI_PREFIX}")

    temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    temp_file_path = temp_file.name
    temp_file.close()

    print("\n🎤 [OPPTAK STARTET] Snakk nå...")

    stream = sd.InputStream(
        samplerate=SAMPLE_RATE, channels=CHANNELS, callback=audio_callback
    )
    stream.start()

    def write_to_file():
        with sf.SoundFile(
            temp_file_path, mode="w", samplerate=SAMPLE_RATE, channels=CHANNELS
        ) as file:
            while is_recording or not audio_queue.empty():
                try:
                    data = audio_queue.get(timeout=0.1)
                    file.write(data)
                except queue.Empty:
                    continue

    global recording_thread
    recording_thread = threading.Thread(target=write_to_file)
    recording_thread.start()


def stop_recording():
    global is_recording, stream, recording_thread, temp_file_path
    if not is_recording:
        return

    is_recording = False

    set_terminal_title(f"{UI_PROCESSING} {UI_PREFIX}")

    if stream:
        stream.stop()
        stream.close()

    if recording_thread:
        recording_thread.join()

    print("🛑 [OPPTAK STOPPET] Sender til lokal server for transkribering...")

    threading.Thread(target=transcribe_and_type, args=(temp_file_path,)).start()


def cancel_recording():
    global is_recording, stream, recording_thread, temp_file_path
    if not is_recording:
        return

    is_recording = False

    if stream:
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass

    if recording_thread:
        recording_thread.join()

    print("❌ [OPPTAK AVBRUTT] Opptaket ble kansellert.")

    if temp_file_path and os.path.exists(temp_file_path):
        try:
            os.remove(temp_file_path)
        except Exception as e:
            print(f"Klarte ikke å slette avbrutt opptak: {e}")

    set_terminal_title(f"{UI_READY} {UI_PREFIX}")


def cancel_recording_if_active():
    if is_recording:
        cancel_recording()


def transcribe_and_type(file_path):
    try:
        with open(file_path, "rb") as f:
            files = {"file": (os.path.basename(file_path), f, "audio/wav")}
            data = {"model": MODEL_NAME}
            if LANGUAGE:
                data["language"] = LANGUAGE
            response = requests.post(API_URL, files=files, data=data, timeout=API_TIMEOUT)

        if response.status_code == 200:
            result = response.json()
            text = result.get("text", "").strip()

            if text:
                print(f"📝 Transkripsjon mottatt: \"{text}\"")
                controller = keyboard.Controller()
                controller.type(text)
            else:
                print("⚠️ Serveren hørte ingen tale i opptaket.")
        else:
            print(f"❌ Feil fra serveren ({response.status_code}): {response.text}")

    except Exception as e:
        print(f"❌ Det oppstod en feil under transkribering: {e}")
    finally:
        set_terminal_title(f"{UI_READY} {UI_PREFIX}")
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            print(f"Klarte ikke å slette midlertidig fil: {e}")


def toggle_recording():
    global is_recording
    if not is_recording:
        start_recording()
    else:
        stop_recording()


def win32_event_filter(msg, data):
    global listener, last_press_time, escape_was_cancelled
    try:
        if IS_WINDOWS and user32:

            if data.vkCode == TOGGLE_VK and TOGGLE_VK is not None:
                ctrl_down = bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
                shift_down = bool(user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)

                if ctrl_down and shift_down:
                    if msg in (256, 260):
                        current_time = time.time()
                        if current_time - last_press_time > TOGGLE_DEBOUNCE:
                            last_press_time = current_time
                            print("⚡ [SNARVEI DETEKTERT] Veksler opptak...")
                            toggle_recording()

                    if listener:
                        listener.suppress_event()

            elif data.vkCode == CANCEL_VK and CANCEL_VK is not None:
                if is_recording or escape_was_cancelled:
                    if msg in (256, 260):
                        escape_was_cancelled = True
                        cancel_recording()
                    elif msg in (257, 261):
                        escape_was_cancelled = False

                    if listener:
                        listener.suppress_event()

    except Exception as e:
        if "SuppressException" in type(e).__name__:
            raise e
        print(f"DEBUG-FEIL i tastaturfilter: {e}", file=sys.stderr)

    return True


def main():
    set_terminal_title(f"{UI_READY} {UI_PREFIX}")

    print("==================================================")
    print("🎙️  Enkel lokal talegjenkjenning startet!")
    print("==================================================")
    print(f"Konfigurasjon lastet fra: {CONFIG_PATH}")
    print(f"Server: {API_URL}")
    print(f"Modell: {MODEL_NAME}")
    print(f"Sample rate: {SAMPLE_RATE} Hz, kanaler: {CHANNELS}")
    print("\nSnarvei registrert:")
    print(f"  - {CONFIG['shortcuts']['toggle']}  (start/stopp opptak)")
    print(f"  - {CONFIG['shortcuts']['cancel']}  (avbryt pågående opptak)")
    print("\n(Trykk Ctrl + C i denne terminalen for å avslutte appen)")
    print("--------------------------------------------------")

    global listener

    if IS_WINDOWS:
        listener = keyboard.Listener(win32_event_filter=win32_event_filter)
    else:
        listener = keyboard.GlobalHotKeys(
            {
                CONFIG["shortcuts"]["toggle"]: toggle_recording,
                CONFIG["shortcuts"]["cancel"]: cancel_recording_if_active,
            }
        )

    listener.start()

    try:
        while True:
            time.sleep(MAIN_LOOP_SLEEP)
    except KeyboardInterrupt:
        print("\nAvslutter talegjenkjenning...")
    finally:
        set_terminal_title(UI_PREFIX)
        listener.stop()


if __name__ == "__main__":
    main()