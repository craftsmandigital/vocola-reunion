import queue
import sys
import tempfile
import threading
import os
import time
import platform  # Brukes til å sjekke operativsystem
import sounddevice as sd
import soundfile as sf
import requests
from pynput import keyboard

# Sjekk hvilket operativsystem vi kjører på
IS_WINDOWS = platform.system() == "Windows"

# Windows-spesifikt oppsett (importeres kun hvis vi er på Windows)
user32 = None
VK_CONTROL = 0x11
VK_SHIFT = 0x10

if IS_WINDOWS:
    import ctypes
    user32 = ctypes.windll.user32

# URL til din lokale faster-whisper-server i Docker
API_URL = "http://192.168.1.200:8000/v1/audio/transcriptions"

# Globale variabler for å kontrollere opptaket
is_recording = False
recording_thread = None
audio_queue = queue.Queue()
stream = None
temp_file_path = None
listener = None  # Holdes global slik at event-filteret på Windows kan nå den

# Tidsstempel for å unngå at taste-repetisjon trigger opptaket flere ganger på rad
last_press_time = 0.0

def set_terminal_title(title):
    """Oppdaterer tittelen på terminalvinduet/fanen på en systemuavhengig måte."""
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

def audio_callback(indata, frames, time, status):
    """Denne funksjonen kalles av sounddevice for hver lydblokk som tas opp."""
    if status:
        print(status, file=sys.stderr)
    audio_queue.put(indata.copy())

def start_recording():
    global is_recording, stream, temp_file_path, audio_queue
    is_recording = True
    audio_queue = queue.Queue()

    # Oppdater tittelen til RØD sirkel (Innspilling)
    set_terminal_title("🔴 [OPPTAK...] Whisper-to-Text")

    # Opprett en midlertidig fil for lagring av WAV-data
    temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    temp_file_path = temp_file.name
    temp_file.close()

    samplerate = 16000  # Whisper foretrekker 16kHz
    channels = 1        # Mono er tilstrekkelig for talegjenkjenning

    print("\n🎤 [OPPTAK STARTET] Snakk nå...")

    # Start lydstrømmen
    stream = sd.InputStream(samplerate=samplerate, channels=channels, callback=audio_callback)
    stream.start()

    # Start en bakgrunnstråd for å skrive data fra køen til filen
    def write_to_file():
        with sf.SoundFile(temp_file_path, mode='w', samplerate=samplerate, channels=channels) as file:
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

    # Oppdater tittelen til TIMEGLASS (Prosessering)
    set_terminal_title("⏳ [BEHANDLER...] Whisper-to-Text")

    # Stopp lydopptaket
    if stream:
        stream.stop()
        stream.close()

    # Vent til skrive-tråden har tømt køen og lagret filen ferdig
    if recording_thread:
        recording_thread.join()

    print("🛑 [OPPTAK STOPPET] Sender til lokal server for transkribering...")

    # Kjør transkribering og skriving i en egen tråd for ikke å blokkere tastaturopptakeren
    threading.Thread(target=transcribe_and_type, args=(temp_file_path,)).start()

def transcribe_and_type(file_path):
    try:
        # Send lydfilen til den lokale OpenAI-kompatible serveren
        with open(file_path, 'rb') as f:
            files = {'file': (os.path.basename(file_path), f, 'audio/wav')}
            data = {'model': 'whisper-1'}
            response = requests.post(API_URL, files=files, data=data, timeout=30)

        if response.status_code == 200:
            result = response.json()
            text = result.get("text", "").strip()

            if text:
                print(f"📝 Transkripsjon mottatt: \"{text}\"")
                # Skriv teksten ut der markøren (cursor) befinner seg
                controller = keyboard.Controller()
                controller.type(text)
            else:
                print("⚠️ Serveren hørte ingen tale i opptaket.")
        else:
            print(f"❌ Feil fra serveren ({response.status_code}): {response.text}")

    except Exception as e:
        print(f"❌ Det oppstod en feil under transkribering: {e}")
    finally:
        # Sett tittelen tilbake til BLÅ sirkel (Klar) når vi er helt ferdig
        set_terminal_title("🔵 [KLAR] Whisper-to-Text")
        
        # Slett den midlertidige lydfilen fra disken
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            print(f"Klarte ikke å slette midlertidig fil: {e}")

def toggle_recording():
    """Veksler mellom å starte og stoppe opptaket."""
    global is_recording
    if not is_recording:
        start_recording()
    else:
        stop_recording()

def win32_event_filter(msg, data):
    """
    Kjører KUN på Windows.
    Blokkerer Windows fra å sende Ctrl + Shift + I videre til aktiv app,
    og trigger handlingen manuelt ved hjelp av Asynchronous Key State.
    """
    global listener, last_press_time
    try:
        if IS_WINDOWS and user32:
            # Virtual Key Code for 'I'-tasten er 0x49 (73)
            if data.vkCode == 0x49:
                ctrl_down = bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
                shift_down = bool(user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
                
                if ctrl_down and shift_down:
                    # 1. Trigger handlingen først (kun på tast-ned: WM_KEYDOWN = 256, WM_SYSKEYDOWN = 260)
                    if msg in (256, 260):
                        current_time = time.time()
                        # Hindrer at Windows auto-repeat trigger funksjonen flere ganger
                        if current_time - last_press_time > 0.4:
                            last_press_time = current_time
                            print("⚡ [SNARVEI DETEKTERT] Veksler opptak...")
                            toggle_recording()
                    
                    # 2. Blokker tasten i Windows ved å bruke suppress_event().
                    # Dette kaster et SuppressException som pynput fanger opp for å blokkere kun denne spesifikke tasten.
                    if listener:
                        listener.suppress_event()
                        
    except Exception as e:
        # Hvis feilen er pynput sitt eget blokkerings-unntak, må vi kaste det videre (raise)
        # slik at pynput sin interne hook oppdager det og blokkerer tasten i Windows.
        if 'SuppressException' in type(e).__name__:
            raise e
        print(f"DEBUG-FEIL i tastaturfilter: {e}", file=sys.stderr)
        
    return True

def main():
    # Sett tittelen til BLÅ sirkel med en gang vi starter opp
    set_terminal_title("🔵 [KLAR] Whisper-to-Text")

    print("==================================================")
    print("🎙️  Enkel lokal talegjenkjenning startet!")
    print("==================================================")
    print("Sørg for at Docker-serveren din kjører på port 8000.")
    print(f"Kjører på: {platform.system()}")
    print("\nSnarvei registrert: [ Ctrl + Shift + I ]")
    print(" - Trykk én gang for å starte opptak.")
    print(" - Trykk igjen for å stoppe, transkribere og skrive.")
    print("\n(Trykk Ctrl + C i denne terminalen for å avslutte appen)")
    print("--------------------------------------------------")

    global listener
    
    # Start lytteren basert på operativsystem
    if IS_WINDOWS:
        # På Windows bruker vi en vanlig Listener i stedet for GlobalHotKeys.
        # Siden vi allerede trigger opptaket manuelt i filteret, unngår vi
        # dermed at handlingen kalles dobbelt.
        listener = keyboard.Listener(win32_event_filter=win32_event_filter)
    else:
        # På Mac/Linux bruker vi standard pynput-snarvei
        listener = keyboard.GlobalHotKeys(
            { '<ctrl>+<shift>+i': toggle_recording }
        )
        
    listener.start()

    # Hovedtråden som holder liv i skriptet og lytter etter Ctrl + C
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nAvslutter talegjenkjenning...")
    finally:
        # Sett tittelen tilbake til normalen når vi avslutter programmet
        set_terminal_title("Whisper-to-Text")
        listener.stop()

if __name__ == "__main__":
    main()()
