# Whisper-to-Text (Lokal Talegjenkjenning)

Dette er en lettvektig Python-klient som lar deg diktere tekst direkte der markøren (cursor) din befinner seg, uansett hvilket program du jobber i. Ved å trykke på den globale snarveien **`Ctrl + Shift + I`** starter og stopper du opptaket, som deretter transkriberes av en lokal `faster-whisper-server` i Docker.

Hvis du ombestemmer deg underveis, kan du når som helst trykke **`Escape`** for å avbryte og slette opptaket.

> **Standard snarveier:** `Ctrl + Shift + I` (toggle) og `Escape` (avbryt). Begge kan endres i [`config.yaml`](#konfigurasjon-configyaml) — server-URL, lydinnstillinger, terminaltitler og annen oppførsel ligger også der.

---

## Hvordan aktive brukerprogrammer påvirkes (Overstyring)

Når du bruker globale snarveier i bakgrunnen, oppstår det ofte konflikter med programmene du faktisk jobber i. Dette programmet bruker en lavnivå system-hook (`win32_event_filter`) på Windows 11 for å fange opp og fjerne tastetrykk før de når andre aktive brukerprogrammer (f.eks. Word, Chrome eller Notepad):

### 1. Selektiv blokkering av Start/Stopp (`Ctrl + Shift + I`)
Vanligvis vil `Ctrl + Shift + I` åpne utviklerverktøyene (DevTools) i nettlesere og kode-editorer. Systemet vårt fanger opp og svelger (suppresserer) `I`-tasten når Ctrl og Shift holdes nede. Det aktive programmet mottar aldri dette tastetrykket, og du slipper uønskede bivirkninger mens du dikterer.

### 2. Avbryting og blokkering med `Escape`
Hvis du trykker på `Escape` for å avbryte et pågående opptak, vil du som regel ikke at dette skal påvirke programmet du skriver i (for eksempel ved at en åpen dialogboks lukkes, eller at du mister markeringen i et tekstfelt). 
* Systemet overvåker `Escape`-tasten, men **kun når opptaket faktisk kjører** (`is_recording == True`).
* Når du trykker `Escape` under et opptak, svelges både *tast-ned* (press) og *tast-opp* (release) for Escape-tasten, slik at det aktive brukerprogrammet forblir helt uberørt.
* Så fort opptaket er avbrutt, fungerer `Escape`-tasten helt som normalt igjen i alle programmer.

### 3. Ingen låsing av tastaturet
Blokkeringen av taster skjer kun i de eksakte millisekundene de definerte kombinasjonene trykkes. Lytteren bruker pynputs interne `SuppressException` for å avbryte akkurat disse hendelsene på lavt nivå. Alle andre tastetrykk og modifikatorer passerer helt uforstyrret gjennom systemet.

### 4. Forebygging av dobbel-triggering
På Windows kjører klienten en ren `keyboard.Listener` i stedet for `keyboard.GlobalHotKeys`. Siden vi allerede håndterer logikken og tastatur-interseptet manuelt, unngår vi parallelle lytte-motorer. Dette eliminerer risikoen for at snarveien trigges to ganger på rad (noe som ellers ville ført til at opptaket startet og stoppet øyeblikkelig, med tomme lydfiler og serverfeil som resultat).

---

## Forutsetninger og installasjon

Sørg for at den lokale Whisper-serveren din kjører i Docker (f.eks. på port `8000`).

Bruk pakkebehandleren `uv` for å sette opp miljøet og installere avhengighetenene lokalt på Windows:

```bash
# Opprett virtuelt miljø og installer pakker
uv add sounddevice soundfile pynput requests pyyaml pyperclip
```

---

## Konfigurasjon (`config.yaml`)

Alle viktige parametre — server-URL, snarveier, lydinnstillinger, terminaltitler og oppførsel — ligger i `config.yaml` i samme mappe som `main.py`. Filen støtter kommentarer, så du kan notere hvorfor du har valgt en bestemt verdi.

### Standardinnhold

```yaml
server:
  url: "http://192.168.1.200:8000/v1/audio/transcriptions"
  model: "whisper-1"
  language: ""          # Tom = auto. Sett f.eks. "no" eller "en" for å låse språket.
  timeout: 30           # Nettverks-timeout i sekunder.

shortcuts:
  toggle: "<ctrl>+<shift>+i"   # Start/stopp opptak (Windows + Mac/Linux).
  cancel: "<esc>"              # Avbryt pågående opptak.

audio:
  sample_rate: 16000    # Hz. Whisper forventer 16000.
  channels: 1           # Mono (1) holder for tale.

behavior:
  toggle_debounce_seconds: 0.4   # Minimum tid mellom to triggere av toggle.
  main_loop_sleep_seconds: 0.5   # Hvor ofte hovedløkken sjekker Ctrl+C.

paste:
  # Hvordan teksten leveres til det aktive programmet.
  method: "ctrl_v"               # direct | ctrl_v | ctrl_shift_v | shift_insert
  paste_delay: 0.05              # Sekunder mellom kopiering og liming.
  # Windows: skjuler teksten for Win+V-historikk og sky-synk (privacy-formater).
  exclude_from_history: true
  # Gjenopprett forrige utklippstavle-innhold etter liming.
  restore_clipboard: true
  # Ventetid (sek) etter liming før utklippstavlen gjenopprettes.
  restore_delay: 0.15

ui:
  title_prefix: "Whisper-to-Text"
  title_ready: "🔵 [Ready]"
  title_recording: "🔴 [Recording...]"
  title_processing: "⏳ [Processing...]"
```

### Viktige ting å vite

* **Hvis `config.yaml` mangler**, faller programmet tilbake til disse standardverdiene og skriver ut en advarsel ved oppstart. Du kan slette filen trygt for å tilbakestille alt.
* **Snarveier** bruker pynput sitt strengformat både på Windows og Mac/Linux, så `<ctrl>+<shift>+i` fungerer likt overalt. Endre til f.eks. `<ctrl>+<shift>+r` hvis `Ctrl+Shift+I` kolliderer med noe i arbeidsflyten din.
* **Windows-blokkeringen** (se neste avsnitt) bruker samme snarvei-streng — VK-koden for hovedtasten avledes automatisk. Du trenger ikke endre noe i koden for å bytte snarvei.
* **Kun delvise overskrivinger** er OK. Hvis du bare vil endre URL, kan du f.eks. lage en minimal fil:

  ```yaml
  server:
    url: "http://10.0.0.50:9000/v1/audio/transcriptions"
  ```

  Alle andre seksjoner bruker da standardverdiene.

### Innlimingsmetoder (`paste.method`)

* **`direct`** — skriver tegn for tegn via pynput. **Eneste metode som ikke rører utklippstavlen.** Tregt for lange tekster; Unicode/emoji kan feile.
* **`ctrl_v`** — legger teksten i utklippstavlen og sender `Ctrl+V`. Universell og rask. Standardvalget.
* **`ctrl_shift_v`** — sender `Ctrl+Shift+V`. Limer inn **ren tekst** i mange apper (nettlesere, VS Code, Slack, Teams). Fungerer derimot ikke i Notepad/Word.
* **`shift_insert`** — klassisk snarvei som finnes i de fleste Windows-apper. Mindre universell enn `Ctrl+V`.

> **Om Windows clipboard history (Win+V):** Alle clipboard-metodene (`ctrl_v`, `ctrl_shift_v`, `shift_insert`) skriver teksten til utklippstavlen. Når `exclude_from_history: true` (standard), settes de samme privacy-formatene som KeePass/Chrome Inkognito bruker, slik at teksten **ikke** dukker opp i Windows sin `Win+V`-historikk eller sky-synk. Med `restore_clipboard: true` (standard) gjenopprettes i tillegg forrige utklippstavle-innhold etter liming.
>
> **Begrensning:** Privacy-formatene er en samarbeidsprotokoll, ikke håndheving. Windows' egen historikk og velkjente verktøy (CopyQ, Ditto, KeePass) respekterer dem, men enkelte tredjeparts clipboard-managere kan fortsatt registrere teksten. `direct` skriver direkte og rører aldri utklippstavlen.

---

## Slik setter du opp skrivebordssnarveien på Windows 11

For å kunne starte talegjenkjenningen med et enkelt dobbeltklikk og ha full kontroll på hvor terminalvinduet plasserer seg, kan du opprette en tilpasset snarvei som utnytter `wt.exe` (Windows Terminal).

### Trinn 1: Opprett snarveien
1. Høyreklikk på skrivebordet ditt og velg **Ny** -> **Snarvei** (Shortcut).
2. I feltet "Skriv inn plasseringen for elementet", lim inn følgende kommando (alt på én linje):
   ```cmd
      C:\Users\jviks\AppData\Local\Microsoft\WindowsApps\wt.exe -w -1 --pos 900,1030 --size 12,10 cmd.exe /k "cd /d C:\Users\jviks\Sync\windows-dev\python\speech-to-text && uv run main.py"

   ```
   *(Erstatt `C:\dev\python\speech-to-text` med den nøyaktige banen til mappen der du har lagret skriptet ditt).*
3. Klikk **Neste**, gi snarveien navnet **Whisper Talegjenkjenning**, og klikk **Fullfør**.

### Trinn 2: Tilpass plassering og størrelse (Valgfritt)
Kommandoen bruker parametere i `wt.exe` som du kan skreddersy til din skjerm:
* **`-w -1`**: Tvinger terminalen til å åpne i et helt eget, frittstående vindu i stedet for en ny fane i en eksisterende terminal.
* **`--pos X,Y`** (f.eks. `--pos 1300,100`): Angir pikselkoordinatene for øverste venstre hjørne av vinduet på skjermen din. Juster disse tallene for å plassere vinduet f.eks. oppe i høyre hjørne eller på en ekstern skjerm.
* **`--size bredde,høyde`** (f.eks. `--size 60,12`): Setter størrelsen på vinduet målt i antall tegn (60 kolonner bredt, 12 rader høyt). Dette gir en kompakt boks som ikke stjeler unødvendig skjermplass.

### Trinn 3: Gi snarveien et mikrofon-ikon
1. Høyreklikk på snarveien du nettopp opprettet på skrivebordet, og velg **Egenskaper** (Properties).
2. Gå til fanen **Snarvei** (Shortcut) og klikk på **Endre ikon...** (Change Icon).
3. I søkefeltet øverst, erstatt teksten med denne banen og trykk Enter:
   ```text
   %SystemRoot%\System32\imageres.dll
   ```
4. Bla deg bortover i listen til du finner en **blå mikrofon** (eller et annet ikon du foretrekker), velg det og klikk **OK** og deretter **Bruk**.

---

## Bruk og visuell tilbakemelding

Når du dobbeltklikker på snarveien på skrivebordet, vil terminalvinduet åpne seg på den angitte posisjonen og automatisk legge seg på toppen av andre vinduer (hvis du har aktivert "Alltid øverst" i Windows Terminal-innstillingene).

Du får løpende visuell tilbakemelding direkte på fane-tittelen i terminalen:
* **`🔵 [KLAR] Whisper-to-Text`**: Systemet er i standby. Trykk `Ctrl + Shift + I` for å starte.
* **`🔴 [OPPTAK...] Whisper-to-Text`**: Mikrofonen tar opp lyd i bakgrunnen. Snakk i vei. 
  * Trykk `Ctrl + Shift + I` på nytt for å stoppe opptaket og sende til transkribering.
  * Trykk **`Escape`** for å avbryte opptaket. Lydfilen slettes og ingenting skrives ut.
* **`⏳ [BEHANDLER...] Whisper-to-Text`**: Lydfilen sendes til Docker og transkriberes. Teksten skrives deretter ut der du har markøren din, før statusen går tilbake til Klar (🔵).
