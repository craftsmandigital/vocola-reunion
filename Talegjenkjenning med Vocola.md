---
created: 2026-07-31
modified: 2026-08-02
tags:
  - kravspek
  - private
---

# Kravspesifikasjon: Talegjenkjenningssystem med Vocola-syntaks

Dette dokumentet beskriver et helhetlig, tverrplattform talegjenkjenningssystem. Systemet kjører lokalt, er optimalisert for lav forsinkelse og bruker en regelbasert tilnærming til å utføre taste- og musestyring basert på syntaksen til Vocola.

---

## 1. Systemarkitektur og kjernekonsept

Systemet løser utfordringen med å skille løpende diktering (fritekst) fra styringskommandoer (tastetrykk og makroer) uten å introdusere forsinkelsen som en stor språkmodell (LLM) krever.

### 1.1 Kjernekonsept: To-språklig heuristikk (Bilingual Heuristics)
Systemet baserer seg på at brukeren dikterer vanlig tekst på ett språk, men uttaler kommandoer på et annet. Dette gjør at vi kan bruke en deterministisk parser i stedet for en ressurskrevende LLM.

*   **Tekstspråk (f.eks. norsk):** Løpende tekst som skal skrives ut i det aktive vinduet.
*   **Kommandospråk (f.eks. engelsk):** Ord og fraser som skal tolkes som handlinger (f.eks. navigering, kopiering, eller vindusstyring).

### 1.2 Eksempel på arbeidsflyt
Brukeren holder nede en hurtigtast og sier følgende i én talestrøm:

> *"Hei Harry, new line, vaskelappen til gullsnoppen min har krympet i tørketrommelen, select line, bold that, new line, blir du med og trener Rumpeldunk likevel på fredag, copy all, switch window, paste that"*

Systemet splitter opp talestrømmen og utfører handlingene i denne rekkefølgen:

| Rekkefølge | Segment fra talestrømmen | Type | Handling som utføres |
| :--- | :--- | :--- | :--- |
| **1** | "Hei Harry, " | **Tekst** | Skriver ut: `Hei Harry, ` |
| **2** | "new line" | **Kommando** | Trykker `{Enter}` |
| **3** | "vaskelappen til gullsnoppen min har krympet i tørketrommelen, " | **Tekst** | Skriver ut: `vaskelappen til gullsnoppen min har krympet i tørketrommelen, ` |
| **4** | "select line" | **Kommando** | Trykker `{Shift+Up}` |
| **5** | "bold that" | **Kommando** | Trykker `{Ctrl+b}` |
| **6** | "new line" | **Kommando** | Trykker `{Enter}` |
| **7** | "blir du med og trener Rumpeldunk likevel på fredag, " | **Tekst** | Skriver ut: `blir du med og trener Rumpeldunk likevel på fredag, ` |
| **8** | "copy all" | **Kommando** | Trykker `{Ctrl+a}{Ctrl+c}` |
| **9** | "switch window" | **Kommando** | Trykker `{Alt+Tab}` |
| **10** | "paste that" | **Kommando** | Trykker `{Ctrl+v}` |

### 1.3 Overordnet dataflyt

```
[ Lokal Klient (PC) ]
  │
  ├─ 1. Hold hurtigtast -> Ta opp lyd fra mikrofon
  └─ 2. Slipp hurtigtast -> Send .wav-fil til Parser-tjenesten via POST
       │
       ▼
[ Parser-tjeneste (Docker: Port 5000) ]
  │
  ├─ 3. Send lyd til Whisper for transkripsjon (med initial prompt)
  │    ▲
  │    └─► [ Whisper-tjeneste (Docker: Port 8000) ]
  │          │
  │          ▼ (Returnerer råtekst)
  ├─ 4. Pre-prosessering: Erstatt globale tekstvariabler ($)
  ├─ 5. Match transkripsjon mot .vcl-regler i minnet (Lark/Regex + Fuzzy Matching)
  └─ 6. Generer en sekvens av tekst og tastehandlinger (JSON)
       │
       ▼
[ Lokal Klient (PC) ]
  │
  └─ 7. Motta JSON og simuler tastetrykk, tekst og tidsforsinkelser lokalt
```

---

## 2. Klient-komponenten (Lokal kjører)

Klienten er en lettvektig bakgrunnsapplikasjon som kjører lokalt på brukerens maskin (med støtte for Windows, macOS og Linux).

### 2.1 Funksjonelle krav
*   **Hurtigtast og opptak:** Appen lytter på en global hurtigtast (f.eks. holde nede `Caps Lock`). Lyd tas opp så lenge tasten holdes nede. Med en gang tasten slippes, sendes opptaket som en `.wav`-fil til Parser-tjenesten.
*   **Tastatursimulering:** Klienten tar imot en JSON-liste med handlinger fra Parser-tjenesten og utfører dem sekvensielt i det aktive vinduet.
*   **Tidsforsinkelser (Delays):** Klienten må kunne legge inn pauser (i millisekunder) mellom handlinger for å sikre at operativsystemet og programmer rekker å respondere (f.eks. etter vindusbytte).

### 2.2 Eksempel på mottatt JSON-sekvens
```json
[
  {"action": "type_text", "text": "Hei Harry, "},
  {"action": "keypress", "keys": ["enter"]},
  {"action": "type_text", "text": "vaskelappen til gullsnoppen min... "},
  {"action": "keypress", "keys": ["shift", "up"]},
  {"action": "keypress", "keys": ["ctrl", "b"]},
  {"action": "keypress", "keys": ["enter"]},
  {"action": "type_text", "text": "blir du med og trener... "},
  {"action": "keypress", "keys": ["ctrl", "a"]},
  {"action": "keypress", "keys": ["ctrl", "c"]},
  {"action": "keypress", "keys": ["alt", "tab"]},
  {"action": "wait", "ms": 150},
  {"action": "keypress", "keys": ["ctrl", "v"]}
]
```

---

## 3. Parser-komponenten (Hjernen)

Parseren kjører som en mikrotjeneste i en Docker-container. Den orkestrerer transkriberingen via Whisper og oversetter resultatet til handlingslisten klienten skal utføre.

### 3.1 Regelkompilering (Lark og Regex)
*   **Oppstart:** Parseren leser brukerens `.vcl`-filer (Vocola-filer) ved oppstart. Den benytter Python-biblioteket `Lark` til å tolke reglene basert på den offisielle [Vocola 3 Formal Grammar](https://vocola.net/v3/FormalGrammar).
*   **Kompilering til minne:** For å fjerne forsinkelse under bruk, oversettes Vocola-reglene til regulære uttrykk (Regex) i minnet. Selve parsingen under bruk skal ta under 1 millisekund.

### 3.2 Pre-prosessering av globale variabler
For å holde regelfilene ryddige, støtter parseren egendefinerte tekstvariabler (f.eks. prompter eller lengre faste tekster). 
*   Under opplasting eller oppstart skanner parseren filene etter linjer som starter med `$` (f.eks. `$MIN_VARIABEL = "tekst...";`).
*   Disse lagres i en lokal ordbok (dictionary) i minnet, fjernes fra kildekoden, og erstattes programmatisk i reglene før de kompileres av Lark.

### 3.3 Segmentering og oppdeling
*   Når transkripsjonen mottas fra Whisper, skanner parseren teksten etter treff på de kompilerte reglene.
*   Teksten deles opp i en strukturert sekvens av enten fri diktering (`type_text`) eller kommandoer (`keypress`, `wait` osv.).

### 3.4 Robusthet og feiltoleranse (Fuzzy Matching)
Whisper kan gjøre mindre fonetiske feil ved overganger mellom norsk og engelsk (f.eks. tolke "paste that" som "pace that" eller "bold that" som "boll that").
*   **Likhetsberegning:** Parseren skal ikke kreve 100 % nøyaktig teksttreff på kommandoer. Den skal beregne tekstlikhet mellom ordene fra Whisper og de definerte `.vcl`-kommandoene ved bruk av Levenshtein-distanse/`RapidFuzz` i Python.
*   **Terskelverdi (Threshold):** Det skal være en konfigurerbar terskelverdi (f.eks. 85 % likhet).
    *   *Over terskelen:* Tolkes som en kommando og utføres.
    *   *Under terskelen:* Behandles som løpende diktering og skrives ut som tekst.

### 3.5 Syntaksfeil og logging
*   Dersom det oppstår feil under tolking av `.vcl`-filene ved oppstart, skal tjenesten logge nøyaktig filnavn, linjenummer og kolonne for feilen, slik at den er enkel for brukeren å rette.

---

## 4. Whisper-tjenesten (Transkribering og optimalisering)

*   **Infrastruktur:** Kjører som en egen Docker-container via `faster-whisper-server` [1].
*   **Modell:** Bruker den norske modellen `Necklace/faster-nb-whisper-large` med `int8_float16`-presisjon for optimal balanse mellom hastighet og nøyaktighet på GPU [1].

### 4.1 Transkripsjonsparametere (Tvungen transkribering)
*   For å unngå at Whisper forsøker å oversette den norske talen til engelsk, må parseren eksplisitt kalle Whisper API-et med parameteren `task="transcribe"` [1]. Dette sikrer at både den norske teksten og de engelske kommandoene returneres nøyaktig slik de ble uttalt.

### 4.2 Dynamisk initial prompt
*   Parser-tjenesten skal automatisk generere en `initial_prompt` som sendes med i forespørselen til Whisper.
*   Prompten skal inneholde en liste over de mest brukte engelske kommandoene fra `.vcl`-filene. Dette "primer" Whisper-modellen til å kjenne igjen de spesifikke engelske ordene i en ellers norsk kontekst.

---

## 5. Støttet Vocola-syntaks (.vcl-filer)

Parseren skal kunne tolke standard Vocola-syntaks i tillegg til våre egne utvidelser (variabler).

### 5.1 Standard Vocola-syntaks

1.  **Enkle definisjoner (Mapping av fraser til taster):**
    ```text
    Copy all = {Ctrl+a}{Ctrl+c};
    Switch window = {Alt+Tab};
    Paste that = {Ctrl+v};
    ```

2.  **Valgfrie ord (Markert med klammer `[]`):**
    ```text
    [please] open browser = {Win+r}chrome{Enter};
    ```

3.  **Alternativer og variabler (Menyvalg sendt som parametere):**
    ```text
    Sort by (Date=e | Sender=n | Subject=s) = {Alt+v}o $1;
    ```

4.  **Tallområder (For repeterende handlinger):**
    ```text
    1..9 (Left | Right) = Keys::Press($2, $1);
    ```

5.  **Fonetiske alternativer (Manuelt definerte aliaser i parentes):**
    ```text
    (Bold | Boll | Bolt) that = {Ctrl+b};
    (Paste | Pace | Paid) that = {Ctrl+v};
    ```

### 5.2 Globale tekstvariabler (Vår utvidelse)
For å unngå lange uleselige tekststrenger inni selve reglene, støtter denne versjonen definisjon av globale konstanter (f.eks. prompter) øverst i `.vcl`-filene:

```text
# Definisjon av globale variabler
$FORMAL_PROMPT = "Vennligst omskriv teksten slik at den blir mer formell og profesjonell.";

# Bruk av variabelen inni en regel
Format selection = {Ctrl+c} AI::Prompt($FORMAL_PROMPT, Clipboard::Get());
```

---

## 6. Infrastruktur og Docker-konfigurasjon

Siden parsingen skjer programmatisk, er det ikke behov for en lokal LLM for standardfunksjonaliteten. Dette holder systemet raskt og ressursvennlig.

### 6.1 Parser Dockerfile
Bygges som en lettvektig Python-container:

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Installer nødvendige Python-pakker
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopier kildekoden for parser-logikken og Lark-grammatikken
COPY . .

# Start FastAPI-serveren
EXPOSE 5000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5000"]
```

Innholdet i `requirements.txt`:
```text
fastapi
uvicorn
lark
pyyaml
requests
python-multipart
rapidfuzz
```

### 6.2 Samlet `docker-compose.yaml`
Parseren og Whisper kommuniserer over et internt Docker-nettverk:

```yaml
services:
  # 1. WHISPER - For lokal, rask talegjenkjenning (Norsk/Engelsk)
  whisper:
    image: fedirz/faster-whisper-server:latest-cuda
    container_name: faster-whisper-server
    restart: unless-stopped
    ports:
      - 8000:8000
    environment:
      - WHISPER__MODEL=Necklace/faster-nb-whisper-large
      - PRELOAD_MODELS=["Necklace/faster-nb-whisper-large"]
      - WHISPER__INFERENCE_DEVICE=cuda
      - WHISPER__TTL=-1
      - WHISPER__COMPUTE_TYPE=int8_float16
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities:
                - gpu
    volumes:
      - whisper_models:/root/.cache/huggingface

  # 2. PARSER - Tolker tale mot .vcl-regler og returnerer tastehandlinger
  vocola-parser:
    build: ./vocola-parser
    container_name: vocola-parser
    restart: unless-stopped
    ports:
      - 5000:5000
    volumes:
      # Mapper din lokale vcl-regeltabell inn i kontaineren
      - ./vcl_rules:/app/rules
    environment:
      - WHISPER_URL=http://faster-whisper-server:8000/v1/audio/transcriptions
      - RULES_DIR=/app/rules
    depends_on:
      - whisper

volumes:
  whisper_models:
```

---

## 7. Lisensiering og kompatibilitet

Vocola 2 is licensed under the **MIT License**. Siden denne implementeringen baserer seg på å skrive en ny parser i Python som tolker syntaksen uavhengig av den originale kildekoden (ved å bruke den formelle grammatikken), er det ingen lisensmessige hindringer for utvikling, modifisering eller distribusjon.

---

## 8. Fremtidig veikart (Kommende versjoner)

Dette avsnittet beskriver funksjonalitet som planlegges implementert i kommende versjoner av systemet.

### 8.1 Avanserte operasjoner på nylig innlest tekst (Søk og formatering)
*   **Konsept:** Brukeren skal kunne målrette formateringskommandoer mot spesifikke ord eller fraser i den nylig innleste teksten, uavhengig av hvor de står i setningen (f.eks. ved å si *"bold tekst Arne Amundsen"*).
*   **Forslag til løsning:**
    1.  **Historikk-buffer:** Parser-tjenesten opprettholder en kort, lokal tekstbuffer over de siste tegnene som har blitt skrevet ut i den aktive økten (f.eks. de siste 500 tegnene).
    2.  **Søk i buffer:** Når en kommando som *"bold tekst [argument]"* trigges, skanner parseren historikk-bufferen etter den spesifikke frasen (f.eks. "Arne Amundsen").
    3.  **Relativ posisjonsberegning:** Parseren finner nøyaktig start- og sluttindeks for frasen i forhold til markørens nåværende posisjon (som antas å være på slutten av bufferen).
    4.  **Tastaturnavigasjon:** Parseren oversetter dette til en automatisk JSON-sekvens for klienten:
        *   Gå til venstre $X$ ganger (avstanden fra markøren til slutten av søkefrasen).
        *   Hold `Shift` og gå til venstre $Y$ ganger (lengden på søkefrasen) for å markere den.
        *   Utfør tastekommandoen for formatering (`{Ctrl+b}`).
        *   Trykk på `End`-tasten for å sende markøren trygt tilbake til slutten av linjen, klar for ny diktering.

### 8.2 Toveis kommunikasjon via Editor-API (Plugins)
*   **Konsept:** Gi talestyringsverktøyet tilgang til å lese og manipulere hele innholdet i et tekstbehandlingsprogram (f.eks. Obsidian, Vim, VS Code) på en sikker og stabil måte, uavhengig av markørposisjon og historikk-buffer.
*   **Forslag til løsning:** 
    *   Det defineres et standardisert og lokalt JSON-API (over WebSockets eller JSON-RPC) i talestyringsverktøyet.
    *   Det utvikles lette plugins/utvidelser i de respektive programmene (f.eks. en TypeScript-plugin i Obsidian, en Lua-plugin i Neovim [1.2.1, 1.2.2]). Disse kobler seg til API-et lokalt.
    *   Når en kommando kjøres, kan talestyringen be programmet om å sende hele tekstbufferen (`get_active_text`), gjøre endringer i minnet (f.eks. søke og erstatte), og skrive det oppdaterte resultatet tilbake (`set_active_text`) i én rask operasjon.

### 8.3 Direkte AI-integrasjon i Vocola-syntaks (`AI::Prompt`)
*   **Konsept:** Koble talekommandoer direkte mot eksterne eller lokale språkmodeller (LLM-er) via standardiserte API-er, slik at man kan bruke stemmeinstruksjoner og variabler til å generere eller forbedre tekst.
*   **Forslag til løsning:**
    *   Det innføres to nye funksjonskall i parseren: `Clipboard::Get()` og `AI::Prompt(system_prompt, bruker_tekst)`.
    *   Når en regel som inneholder `AI::Prompt` trigges, vil klienten instrueres til å hente markert tekst (via utklippstavlen), sende denne sammen med den definerte systemprompten til KI-en (f.eks. OpenAI, Anthropic eller en lokal Ollama-modell), og automatisk skrive det ferdige KI-genererte resultatet tilbake på skjermen [2].