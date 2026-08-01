# Whisper-to-Text (Lokal Talegjenkjenning)

Dette er en lettvektig Python-klient som lar deg diktere tekst direkte der markøren (cursor) din befinner seg, uansett hvilket program du jobber i. Ved å trykke på den globale snarveien **`Ctrl + Shift + I`** starter og stopper du opptaket, som deretter transkriberes av en lokal `faster-whisper-server` i Docker.

---

## Hvordan aktive brukerprogrammer påvirkes (Overstyring)

Når du bruker globale snarveier i bakgrunnen, oppstår det ofte konflikter med programmene du faktisk jobber i. For eksempel vil `Ctrl + Shift + I` i nettlesere (Chrome, Edge) eller kode-editorer (VS Code) vanligvis åpne utviklerverktøyene (DevTools).

Dette programmet er designet spesifikt for å unngå slike bivirkninger på Windows 11:

### 1. Selektiv blokkering (Intersept)
Klienten bruker en lavnivå Windows-hook (`win32_event_filter`). Hver gang en tast trykkes på tastaturet, blir den analysert før den sendes til Windows-kjernen:
* Hvis du trykker `Ctrl + Shift + I`, blir tastetrykket fanget opp og **svelget** (suppressert). 
* Det aktive programmet du jobber i (f.eks. Word, Chrome eller Notepad) mottar aldri dette tastetrykket. Dermed unngår du at uønskede menyer eller verktøy åpner seg i bakgrunnen mens du prøver å diktere.

### 2. Ingen låsing av tastaturet
Blokkeringen skjer kun akkurat i det millisekundet du trykker ned og slipper `I`-tasten mens `Ctrl` og `Shift` holdes nede. Lytteren bruker pynputs interne `SuppressException` for å avbryte akkurat denne spesifikke hendelsen. Alle andre tastetrykk og modifikatorer (som å bruke `Ctrl + C`, `Shift + I` for stor I, osv.) passerer helt uforstyrret gjennom systemet.

### 3. Forebygging av dobbel-triggering
På Windows kjører klienten en ren `keyboard.Listener` i stedet for `keyboard.GlobalHotKeys`. Dette er fordi vi allerede håndterer logikken og tastatur-interseptet manuelt. Ved å unngå parallelle lytte-motorer elimineres risikoen for at snarveien trigges to ganger på rad (noe som ellers ville ført til at opptaket startet og stoppet øyeblikkelig, med tomme lydfiler og serverfeil som resultat).

---

## Forutsetninger og installasjon

Sørg for at den lokale Whisper-serveren din kjører i Docker (f.eks. på port `8000`).

Bruk pakkebehandleren `uv` for å sette opp miljøet og installere avhengighetene lokalt på Windows:

```bash
# Opprett virtuelt miljø og installer pakker
uv add sounddevice soundfile pynput requests
```

---

## Slik setter du opp skrivebordssnarveien på Windows 11

For å kunne starte talegjenkjenningen med et enkelt dobbeltklikk og ha full kontroll på hvor terminalvinduet plasserer seg, kan du opprette en tilpasset snarvei som utnytter `wt.exe` (Windows Terminal).

### Trinn 1: Opprett snarveien
1. Høyreklikk på skrivebordet ditt og velg **Ny** -> **Snarvei** (Shortcut).
2. I feltet "Skriv inn plasseringen for elementet", lim inn følgende kommando (alt på én linje):
   ```cmd
   wt.exe -w -1 --pos 1300,100 --size 60,12 cmd.exe /k "cd /d C:\dev\python\speech-to-text && uv run main.py"
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
* **`🔴 [OPPTAK...] Whisper-to-Text`**: Mikrofonen tar opp lyd i bakgrunnen. Snakk i vei. Trykk `Ctrl + Shift + I` på nytt for å stoppe.
* **`⏳ [BEHANDLER...] Whisper-to-Text`**: Lydfilen sendes til Docker og transkriberes. Teksten skrives deretter ut der du har markøren din, før statusen går tilbake til Klar (🔵).
