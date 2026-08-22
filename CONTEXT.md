# Talestyring (speech-to-text)

Lokal talestyring i to moduser: norsk diktering til tekstfelt og engelsk stemmekommando som utløser tastaturhandlinger. Beregning skjer på en GPU-server; klienten tar opp lyd og utfører handlinger.

## Language

### Moduser

**Talemodus (diktering)**:
Modus der talt norsk transkriberes og limes inn som tekst der markøren står.
_Avoid_: dictation mode, whisper-modus, stor modell

**Kommandomodus**:
Modus der korte engelske kommandoer oversettes til en handlingssekvens av tastetrykk.
_Avoid_: commando-modus, makromodus

### Opptak

**Toggle-opptak**:
Interaksjonsmodell der samme tast starter og stopper et opptak.
_Avoid_: Push-to-Talk, PTT, hold-og-snakke

**Energi-trimming**:
Klientens klipping av stillhet i kantene av et opptak før sending.
_Avoid_: VAD, silence removal

**RMS-gate**:
Terskel som forkaster opptak med for lavt lydnivå, for kort varighet eller for lite faktisk taleenergi (`min_loud_ms`: minimum ms med vinduer over terskelen) før de sendes.

**Opptakstak**:
Maksimal varighet på ett opptak; ved overskridelse sendes klippet automatisk.

### Grammatikk

**Regel**:
En talt frase knyttet til én handlingssekvens. Kan inneholde valggrupper i parentes, f.eks. `open (chrome | terminal)`.
_Avoid_: kommando, pattern

**Grammatikk**:
Det fullstendige regelsettet som definerer alt gyldig kommandovokabular.
_Avoid__: rules file, vokabular

**Valggruppe**:
Parenteserte alternativer inni en regel der brukeren velger ett ord.

### Resultat

**Handlingssekvens**:
Den kronologiske listen over `keypress`-, `wait`- og `type_text`-operasjoner en gjenkjent regel utløser.
_Avoid_: macro, key sequence

**Konvolutt**:
JSON-responsen fra serveren: status, hørt tekst, matchet regel, handlingssekvens og timing.
_Avoid_: respons, payload

**Ukjent kommando**:
Hørt tekst som ikke matcher noen regel; ingen taster trykkes.
_Avoid_: UNKNOWN_COMMAND (brukes kun som maskinverdi i konvolutten)

### Tjenester

**Kommandotjenesten**:
Serverprosessen som mottar rå PCM, transkriberer med lettvektsmodellen, parser mot grammatikken og returnerer konvolutten.
_Avoid_: koordinator, ASR-tjeneste, wrapper

**Dikteringstjenesten**:
Den eksisterende servertjenesten som kjører den store norske modellen.
_Avoid_: tale-server
