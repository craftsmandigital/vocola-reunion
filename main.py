import os
import platform
import queue
import struct
import sys
import tempfile
import threading
import time

import requests
import sounddevice as sd
import soundfile as sf
import yaml
import pyperclip
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
    "paste": {
        "method": "ctrl_v",
        "paste_delay": 0.05,
        "exclude_from_history": True,
        "restore_clipboard": True,
        "restore_delay": 0.15,
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

PASTE_METHOD = CONFIG["paste"]["method"]
PASTE_DELAY = float(CONFIG["paste"]["paste_delay"])
EXCLUDE_FROM_HISTORY = bool(CONFIG["paste"]["exclude_from_history"])
RESTORE_CLIPBOARD = bool(CONFIG["paste"]["restore_clipboard"])
RESTORE_DELAY = float(CONFIG["paste"]["restore_delay"])

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


_PRIVACY_FORMATS = [
    "ExcludeClipboardContentFromMonitorProcessing",
    "CanIncludeInClipboardHistory",
    "CanUploadToCloudClipboard",
    "Clipboard Viewer Ignore",
]
_PRIVACY_OK_REPORTED = False


def _open_clipboard_with_retry(timeout=3.0):
    """Åpner utklippstavlen med retry (løser 'clipboard busy' etter liming)."""
    import ctypes

    user32 = ctypes.windll.user32
    user32.OpenClipboard.restype = ctypes.c_bool
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    deadline = time.time() + timeout
    while True:
        if user32.OpenClipboard(None):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.05)


def _verify_privacy_formats_windows():
    """Leser tilbake privacy-formatene og returnerer hvilke som mangler."""
    import ctypes

    user32 = ctypes.windll.user32
    user32.RegisterClipboardFormatW.restype = ctypes.c_uint
    user32.RegisterClipboardFormatW.argtypes = [ctypes.c_wchar_p]
    user32.IsClipboardFormatAvailable.restype = ctypes.c_bool
    user32.IsClipboardFormatAvailable.argtypes = [ctypes.c_uint]
    if not _open_clipboard_with_retry():
        return list(_PRIVACY_FORMATS)
    missing = []
    try:
        for name in _PRIVACY_FORMATS:
            fmt = user32.RegisterClipboardFormatW(name)
            if not fmt or not user32.IsClipboardFormatAvailable(fmt):
                missing.append(name)
    finally:
        user32.CloseClipboard()
    return missing


def _set_clipboard_private_windows(text):
    """Setter tekst i utklippstavlen med private formater (Win+V / sky-synk ignorerer den)."""
    import ctypes

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    user32.RegisterClipboardFormatW.restype = ctypes.c_uint
    user32.RegisterClipboardFormatW.argtypes = [ctypes.c_wchar_p]
    user32.OpenClipboard.restype = ctypes.c_bool
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.EmptyClipboard.restype = ctypes.c_bool
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.CloseClipboard.restype = ctypes.c_bool
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]

    def register(name):
        return user32.RegisterClipboardFormatW(name)

    def set_dword(fmt, value):
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, 4)
        if not h:
            raise ctypes.WinError()
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            raise ctypes.WinError()
        ctypes.memmove(ptr, struct.pack("<I", value), 4)
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(fmt, h):
            raise ctypes.WinError()

    if not _open_clipboard_with_retry():
        raise ctypes.WinError()
    try:
        if not user32.EmptyClipboard():
            raise ctypes.WinError()

        payload = text.encode("utf-16-le") + b"\x00\x00"
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
        if not h:
            raise ctypes.WinError()
        ptr = kernel32.GlobalLock(h)
        ctypes.memmove(ptr, payload, len(payload))
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            raise ctypes.WinError()

        set_dword(register("ExcludeClipboardContentFromMonitorProcessing"), 1)
        set_dword(register("CanIncludeInClipboardHistory"), 0)
        set_dword(register("CanUploadToCloudClipboard"), 0)
        set_dword(register("Clipboard Viewer Ignore"), 1)
    finally:
        user32.CloseClipboard()


def _get_clipboard_text_windows():
    """Leser gjeldende tekst fra utklippstavlen (None hvis ingen tekst)."""
    import ctypes

    CF_UNICODETEXT = 13
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    user32.OpenClipboard.restype = ctypes.c_bool
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.GetClipboardData.restype = ctypes.c_void_p
    user32.GetClipboardData.argtypes = [ctypes.c_uint]
    user32.IsClipboardFormatAvailable.restype = ctypes.c_bool
    user32.IsClipboardFormatAvailable.argtypes = [ctypes.c_uint]
    user32.CloseClipboard.restype = ctypes.c_bool
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]

    if not _open_clipboard_with_retry():
        return None
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        size = kernel32.GlobalSize(h)
        if not size:
            return None
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            return None
        try:
            raw = ctypes.string_at(ptr, size)
        finally:
            kernel32.GlobalUnlock(h)
        return raw.decode("utf-16-le").rstrip("\x00")
    finally:
        user32.CloseClipboard()


def _empty_clipboard_windows():
    """Tømmer utklippstavlen (brukes når forrige innhold ikke var tekst). Returnerer om det lyktes."""
    import ctypes

    user32 = ctypes.windll.user32
    user32.EmptyClipboard.restype = ctypes.c_bool
    if not _open_clipboard_with_retry():
        return False
    try:
        return bool(user32.EmptyClipboard())
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text, private=True):
    """Setter tekst i utklippstavlen. På Windows med private=True skjules den for Win+V/sky."""
    global _PRIVACY_OK_REPORTED
    if IS_WINDOWS and private:
        try:
            _set_clipboard_private_windows(text)
            missing = _verify_privacy_formats_windows()
            if missing:
                print(f"⚠️ Privacy-formater MANGLER: {', '.join(missing)}")
            elif not _PRIVACY_OK_REPORTED:
                _PRIVACY_OK_REPORTED = True
                print("✔ Privacy-formater bekreftet (4/4) — teksten skjules for Win+V.")
            return
        except Exception as e:
            print(f"⚠️ Kunne ikke sette privat utklippstavle, bruker vanlig kopi: {e}")
    pyperclip.copy(text)


def get_clipboard_text():
    """Snapshot av gjeldende utklippstavle-tekst (None hvis tom/ikke tekst)."""
    if IS_WINDOWS:
        try:
            return _get_clipboard_text_windows()
        except Exception as e:
            print(f"⚠️ Kunne ikke lese utklippstavlen: {e}")
            return None
    try:
        return pyperclip.paste()
    except Exception:
        return None


def restore_clipboard(snapshot):
    """Gjenoppretter utklippstavlen til et tidligere snapshot (privat, så ingen duplikat i Win+V)."""
    if IS_WINDOWS:
        if snapshot is None:
            if not _empty_clipboard_windows():
                print("⚠️ Kunne ikke tømme utklippstavlen (opptatt etter 3 s?).")
            return
        try:
            _set_clipboard_private_windows(snapshot)
            return
        except Exception as e:
            print(f"⚠️ Kunne ikke gjenopprette utklippstavlen: {e}")
    if snapshot is not None:
        pyperclip.copy(snapshot)


def paste_text(text, method, delay):
    """Leverer teksten til det aktive programmet etter valgt metode."""
    if method == "direct":
        keyboard.Controller().type(text)
        return

    snapshot = get_clipboard_text() if RESTORE_CLIPBOARD else None

    try:
        set_clipboard_text(text, private=EXCLUDE_FROM_HISTORY)
        if delay > 0:
            time.sleep(delay)

        controller = keyboard.Controller()
        if method == "ctrl_v":
            with controller.pressed(keyboard.Key.ctrl):
                controller.press("v")
                controller.release("v")
        elif method == "ctrl_shift_v":
            with controller.pressed(keyboard.Key.ctrl):
                with controller.pressed(keyboard.Key.shift):
                    controller.press("v")
                    controller.release("v")
        elif method == "shift_insert":
            with controller.pressed(keyboard.Key.shift):
                controller.press(keyboard.Key.insert)
                controller.release(keyboard.Key.insert)
        else:
            print(f"⚠️ Ukjent paste-metode '{method}', faller tilbake til ctrl_v.")
            with controller.pressed(keyboard.Key.ctrl):
                controller.press("v")
                controller.release("v")
    finally:
        if RESTORE_CLIPBOARD:
            time.sleep(RESTORE_DELAY)
            restore_clipboard(snapshot)


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
                paste_text(text, PASTE_METHOD, PASTE_DELAY)
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
    print(f"\nInnlimingsmetode: {PASTE_METHOD}")
    print("(Trykk Ctrl + C i denne terminalen for å avslutte appen)")
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