# AI-videoredigering – skuleprosjekt

Ein lokal prototype for eit framtidig abonnementsbasert videoverktøy, laga med ein enkel struktur som passar eit VG2 IT-prosjekt. Du kan opprette prosjekt, laste opp videoklipp, velje innstillingar og eksportere ein ekte MP4-video med FFmpeg.

**Denne versjonen er ein lokal demonstrasjon utan innlogging og utan AI-analyse.** Han skal berre køyrast på di eiga maskin. Betaling og automatisk publisering er ikkje aktiverte.

## Kva fungerer?

- Prosjektoversikt og arbeidsflate med lagring i SQLite.
- Opplasting av fleire videoklipp og kontroll av filene på serveren.
- Ønskt lengd i minutt og sekund, også for videoar som varer fleire minutt.
- Liggjande 16:9, ståande 9:16 og kvadratisk 1:1.
- Val mellom kantar som bevarer heile biletet, og beskjering som fyller ramma.
- Enkel redigering som klipper og set saman verkelege videofiler.
- Bakgrunnsjobbar med reell status, feilmeldingar, førehandsvising og nedlasting.
- Lokale publiseringsplanar med godkjenning, kanal og tidspunkt.
- Ei side med faktisk lokal ressursbruk og tydeleg merkte abonnementsdøme.

Prosjekt, videofiler, jobbstatus og publiseringsplanar ligg på disk og finst etter at sida blir oppdatert. Dei blir òg bevarte når serveren blir starta på nytt. Ein jobb som vart avbroten under behandling, blir merkt mislukka ved neste oppstart; start då ein ny eksport. Jobbar som berre stod i kø, kan behandlast vidare.

## Start på Windows i VS Code

### 1. Installer verktøya

Du treng:

- [Git](https://git-scm.com/downloads).
- [Visual Studio Code](https://code.visualstudio.com/).
- [Python 3.11 eller nyare](https://www.python.org/downloads/windows/), med Python Launcher (`py`).
- FFmpeg og ffprobe. [Den offisielle FFmpeg-sida](https://ffmpeg.org/download.html) lenkjer til ferdige Windows-bygg.

FFmpeg kan installerast frå PowerShell med Windows Package Manager:

```powershell
winget install --id Gyan.FFmpeg --exact
```

Lukk og opne terminalen på nytt etter installasjonen, slik at oppdatert `PATH` blir lesen. Kontroller at begge programma er tilgjengelege:

```powershell
py -3 --version
git --version
ffmpeg -version
ffprobe -version
```

Dersom du installerer FFmpeg manuelt, legg mappa som inneheld `ffmpeg.exe` og `ffprobe.exe` til i `PATH`, eller bruk dei valfrie miljøvariablane nedanfor.

### 2. Hent repositoryet og opne det

Køyr dette i PowerShell i mappa der du vil lagre prosjektet:

```powershell
git clone https://github.com/e-liaber2-crypto/Ai-proskjekt.git
cd Ai-proskjekt
code .
```

Har du alt prosjektmappa lokalt, opnar du den same mappa med **File → Open Folder** i VS Code. Du treng ikkje klone eit ekstra eksemplar. Opne deretter **Terminal → New Terminal**. Alle kommandoane nedanfor skal køyrast frå prosjektroten, der `requirements.txt` ligg.

### 3. Installer Python-avhengigheitene

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

Vi brukar heile stien til Python i det virtuelle miljøet. Dermed treng du ikkje aktivere miljøet eller endre PowerShell sin execution policy. Vel gjerne `.venv` som Python-interpreter i VS Code òg.

### 4. Start nettsida

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Opne **http://127.0.0.1:8000** i nettlesaren. Stopp serveren med `Ctrl+C`.

Bruk denne serveren til heile appen. Å dobbeltklikke `index.html` eller bruke Live Server startar ikkje API-et eller videobehandlinga. Vi startar utan `--reload`, slik at filendringar ikkje avbryt ein aktiv eksport. Start serveren på nytt manuelt etter endringar i Python-koden når ingen jobb køyrer.

### Andre operativsystem

Installer FFmpeg og Python gjennom ein eigna pakkebehandlar. Frå prosjektroten:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Miljøvariablar

`.env.example` inneheld berre døme, ingen hemmelegheiter. Kopier han til `.env` dersom fila ikkje finst frå før. Standardoppsettet treng ingen API-nøklar.

| Variabel | Bruk |
| --- | --- |
| `DATA_DIR` | Valfri plassering for databasen og videofilene. Standard er `data` i prosjektroten. |
| `FFMPEG_BIN` | Valfri full sti til FFmpeg dersom programmet ikkje ligg i `PATH`. |
| `FFPROBE_BIN` | Valfri full sti til ffprobe dersom programmet ikkje ligg i `PATH`. |

På Windows kan stiar skrivast med skråstrek, til dømes `C:/Verktoy/ffmpeg/bin/ffmpeg.exe`. Start serveren på nytt etter endringar i `.env`.

Det er ikkje implementert ein AI- eller transkripsjonsleverandør enno. Å leggje til ein tilfeldig API-nøkkel aktiverer derfor ikkje AI eller teksting. Eventuelle framtidige nøklar skal berre lesast på serveren og aldri leggjast i HTML, JavaScript eller Git.

## Slik prøver du hovudflyten

1. Opprett eit prosjekt med eit namn.
2. Last opp eitt eller fleire små videoklipp.
3. Skriv kva du ønskjer å lage. I denne versjonen blir teksten lagra, men ikkje tolka av AI.
4. Vel lengd, format, kantar eller beskjering og redigeringsstil.
5. Start behandling. Statusen kjem frå den faktiske bakgrunnsjobben.
6. Spel av resultatet og last ned MP4-fila.
7. Du kan lagre ein lokal publiseringsplan etter at du har godkjent videoen, kanalen og tidspunktet.

Prøv gjerne både eit klipp med lyd og eit utan lyd. Eksporten får eit lydspor òg når ei kjelde manglar lyd.

### Korleis den enkle redigeringa verkar

Motoren tek eit utdrag frå starten av kvart kjeldeklipp, i opplastingsrekkjefølgje. Den ønskte lengda blir fordelt proporsjonalt mellom klippa. Dette er deterministisk trimming, ikkje ei vurdering av kva innhaldet handlar om. Han vel ikkje automatisk dei mest interessante augneblikka.

Stilen styrer enkle fade-inn- og fade-ut-effektar: roleg brukar inntil 0,6 sekund, profesjonell inntil 0,25 sekund, og energisk brukar harde klipp. Svært korte utdrag får tilpassa fade-lengd.

Eksporten er H.264-video med AAC-lyd i MP4, med 24 bilete i sekundet:

| Format | Oppløysing |
| --- | --- |
| Liggjande 16:9 | 1280 × 720 |
| Ståande 9:16 | 720 × 1280 |
| Kvadratisk 1:1 | 720 × 720 |

### Grenser i førsteversjonen

| Grense | Verdi |
| --- | --- |
| Storleik per kjeldefil | 200 MiB |
| Klipp per prosjekt | 12 |
| Lengd per kjeldeklipp | 30 minutt |
| Ønskt eksportlengd | 1 sekund–10 minutt |
| Lokal lagringsgrense | 2 GiB |

Ønskt eksportlengd må vere innanfor den samla lengda til kjeldene. Appen skal forklare når materialet er for kort; han strekkjer eller repeterer ikkje klippa for å fylle tida. Lengda kan avvike litt på grunn av avrunding til heile videobilete.

## Testar

Testane brukar ei mellombels datamappe og små, syntetiske videofiler. Dei krev at FFmpeg og ffprobe er installerte. Køyr frå prosjektroten:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

På macOS/Linux:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Testane er laga for å kontrollere opplasting, verkeleg videoeksport og dekoding, format og lengd, ugyldige førespurnader, lagring og lokale publiseringsplanar. Sjå testutskrifta for kva som faktisk vart køyrt og bestod på di maskin. Ein manuell nettlesartest bør òg kontrollere avspeling, nedlasting, mobiloppsett og tastaturnavigasjon.

Under utviklinga vart åtte integrasjonstestar køyrde i Linux-skymiljøet med Python 3.12 og FFmpeg 7.1. Dei omfattar alle tre format, kjelder med og utan lyd, ein 61-sekunds eksport, ugyldige filer, feila jobbar og gjenoppretting etter omstart. Chromium vart òg brukt til heile flyten frå prosjektoppretting til avspeling, nedlasting og godkjend lokal plan. Mobiloppsettet vart kontrollert ned til 320 piksel. Windows-kommandoane over er skrivne for PowerShell, men er ikkje køyrde på ein Windows-maskin her.

## Struktur og viktige val

```text
Ai-proskjekt/
├── app/
│   ├── main.py          # API, SQLite og kø for bakgrunnsjobbar
│   └── media.py         # Mediekontroll, klippeplan og FFmpeg
├── tests/               # Integrasjonstestar med ekte videofiler
├── index.html           # Innhald og struktur i grensesnittet
├── styles.css           # Design og mobiltilpassing
├── app.js               # Nettlesarlogikk og API-kall
├── requirements.txt     # Python-avhengigheiter
├── .env.example         # Konfigurasjonsdøme utan hemmelegheiter
└── data/                # Lokal database og videoar, ikkje i Git
```

- **FastAPI** tek imot førespurnader og validerer innstillingar på serveren. Vanleg HTML, CSS og JavaScript held frontend enkel utan eit byggjesteg.
- **SQLite** lagrar prosjekt, klipp, jobbar og planar i `data/app.db`. Det er nok for ein lokal brukar og krev ikkje ein eigen databaseserver.
- **Éin bakgrunnsarbeidar** køyrer videojobbar i kø, slik at HTTP-serveren kan svare medan FFmpeg arbeider. Dette avgrensar òg samtidig bruk av CPU og minne. Køyr berre éin serverprosess; ikkje bruk fleire Uvicorn-workers for denne prototypen.
- **FFmpeg** produserer den faktiske videofila. Klippeplanen blir validert før han blir køyrd. Framdrifta blir oppdatert ved verkelege delsteg, ikkje av ein simulert nedteljar.
- **Filkontroll** omfattar både innstillingar og faktisk medieinnhald. Opplasta filer får genererte interne namn. FFmpeg blir starta med argumentlister utan shell, slik at brukartekst ikkje blir ein kommando. Mellombelse renderfiler blir rydda opp etter jobben.
- **Lokale data** og `.env` er ignorerte av Git. Ikkje legg inn opplasta videoar, databasen eller API-nøklar manuelt. Sletting av `data` fjernar lokale prosjekt og videoar; ta vare på filer du treng først.

## Demonstrasjon og vidare arbeid

Abonnementssida viser døme på moglege planar og prisar. Det finst inga aktiv betaling, betalingsintegrasjon eller abonnementsinnkrevjing. Brukstala er lokale, og er ikkje eit ferdig system for månadsfakturering.

Publiseringskalenderen lagrar berre planar. Kanalnamn som YouTube, Instagram eller TikTok betyr ikkje at kontoen er tilkopla. Ingen video blir send til ei plattform. Tidspunkt blir lagra med tidssoneinformasjon, og identiske planleggingsforsøk skal ikkje opprette doble planar.

Naturlege neste steg er:

1. Ein serverbasert AI-adapter som analyserer kjeldene og leverer ein klippeplan som blir validert før FFmpeg får han. Valfri teksting krev ei eiga transkripsjonsløysing.
2. Innlogging og tilgangskontroll, slik at kvar brukar berre får tilgang til sine eigne prosjekt og filer.
3. Ekte abonnementsgrenser og betalingsintegrasjon i testmodus.
4. Offisielle API-ar og autorisert kontotilkopling for publisering, basert på oppdatert dokumentasjon for kvar plattform.

Tenesta må få meir tilgangskontroll, driftsoppsett og ressursstyring før ho kan vere ei offentleg fleirbrukarteneste. Denne prototypen skal starte på `127.0.0.1`, ikkje gjerast offentleg tilgjengeleg.
