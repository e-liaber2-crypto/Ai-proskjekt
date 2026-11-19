# AI-videoredigering – skuleprosjekt

Ein lokal prototype for eit framtidig abonnementsbasert videoverktøy, laga med ein enkel struktur som passar eit VG2 IT-prosjekt. Du kan opprette prosjekt, laste opp videoklipp, velje innstillingar og eksportere ein ekte MP4-video med FFmpeg. Med ei konfigurert Gemini-kopling kan Google analysere videoane og foreslå kva utdrag som skal brukast, i kva rekkjefølgje.

**Standardoppsettet er ein lokal demonstrasjon utan innlogging.** For ein passordverna lærardemo på Render, sjå [DEPLOY.md](DEPLOY.md). Gratisdemoen har mellombels lagring og støttar ikkje Windows-forteljarstemma. Enkel redigering fungerer utan API-nøkkel. Gemini-analyse er valfri og sender analysemateriale til Google. Betaling og automatisk publisering er ikkje aktiverte.

## Render og kodekontroll

Sjå [DEPLOY.md](DEPLOY.md) for førstegongsoppsett og oppdatering av ei eksisterande Render-teneste. GitHub Actions testar appen og Docker-oppsettet før ein Blueprint med `checksPass` deployar.

Koden er delt etter ansvar: `app/main.py` handterer prosjekt, lagring og jobbkø, `app/hosting.py` handterer vertsnamn, tilgangsvern og HTTP-grenser, og `app/schemas.py` validerer inndata. `app/media.py` køyrer FFmpeg, medan Gemini, videotitlar og forteljarstemme har eigne modular. Grensesnittet ligg framleis i `index.html`, `styles.css` og `app.js`.

## Kva fungerer?

- Prosjektoversikt og arbeidsflate med lagring i SQLite.
- Opplasting av fleire videoklipp og kontroll av filene på serveren.
- Hurtigval for kort og lang video, tilpassa lengda på materialet, og fri innstilling i minutt og sekund.
- Liggjande 16:9, ståande 9:16 og kvadratisk 1:1.
- Val mellom kantar som bevarer heile biletet, og beskjering som fyller ramma.
- Enkel redigering som klipper og set saman verkelege videofiler.
- Valfri Gemini-analyse med strukturert klippeplan, validering på serveren og eksport gjennom den same FFmpeg-motoren.
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

`.env.example` inneheld berre døme, ingen hemmelegheiter. Kopier han til `.env` dersom fila ikkje finst frå før. Enkel redigering treng ingen API-nøklar.

| Variabel | Bruk |
| --- | --- |
| `FFMPEG_THREADS` | Talet på FFmpeg-trådar (1–4). Standard er 2 lokalt og 1 i Render-Blueprint-en. |
| `DATA_DIR` | Valfri plassering for databasen og videofilene. Standard er `data` i prosjektroten. |
| `FFMPEG_BIN` | Valfri full sti til FFmpeg dersom programmet ikkje ligg i `PATH`. |
| `FFPROBE_BIN` | Valfri full sti til ffprobe dersom programmet ikkje ligg i `PATH`. |
| `GEMINI_API_KEY` | Berre nødvendig for Gemini-analyse. Ei eksisterande miljøvariabel blir brukt direkte. Alternativt kan nøkkelen liggje i lokal `.env`, aldri i frontend eller Git. |
| `GEMINI_MODEL` | Valfri modell for analyse. Standard er `gemini-3.8-flash`. Modelltilgang og kvotar avheng av Google-prosjektet ditt. |

På Windows kan stiar skrivast med skråstrek, til dømes `C:/Verktoy/ffmpeg/bin/ffmpeg.exe`. Start serveren på nytt etter endringar i `.env`.

### Slå på Gemini på di eiga maskin

1. Har du allereie `GEMINI_API_KEY` i miljøet som startar serveren, blir han brukt direkte. Du treng ikkje lage ein ny nøkkel eller leggje same verdi i `.env`.
2. Berre dersom miljøvariabelen manglar: bruk ein nøkkel frå [Google AI Studio](https://aistudio.google.com/apikey), og legg han i den lokale `.env`-fila i VS Code. Bruk oppføringa i `.env.example` som rettleiing. Ikkje del nøkkelen i chat, skjermbilete, loggar eller Git.
3. Lat `GEMINI_MODEL` vere `gemini-3.8-flash`, eller vel ein annan modell du har tilgang til som støttar videoinndata og strukturert JSON-utdata.
4. Start serveren på nytt. Vel Gemini-modus i arbeidsflata når du vil analysere kjeldeklippa.

Appen brukar den offisielle Python-pakka `google-genai==2.34.0`. Nøkkelen blir berre brukt på serveren når SDK-klienten blir oppretta; han blir ikkje send til nettlesaren. Integrasjonen vel uttrykkeleg `GEMINI_API_KEY`, slik at ei eventuell anna `GOOGLE_API_KEY` ikkje blir brukt ved eit uhell.

Gemini-analyse kan bruke kvote eller koste pengar. Google får bilete frå klippa på Render, eller video og lyd ved lokal køyring. Sendinga krev samtykke for kvar AI-jobb. Bruk berre klipp du har rett til å sende dit, og sjekk vilkåra, prisane og kvoten for kontoen din. Undertekstar og transkripsjon er ikkje integrerte.

## Slik prøver du hovudflyten

1. Opprett eit prosjekt med eit namn.
2. Last opp eitt eller fleire små videoklipp.
3. Vel enkel redigering utan AI, eller Gemini-analyse dersom ho er konfigurert. Skriv kva du ønskjer å lage; teksten blir berre tolka i Gemini-modus.
4. Vel lengd, format, kantar eller beskjering og redigeringsstil. Kortvalet tilpassar seg korte klipp. I Gemini-modus må du òg godkjenne sendinga til Google. Appen går vidare med enkel redigering dersom Gemini feilar.
5. Start behandling. Statusen kjem frå den faktiske bakgrunnsjobben.
6. Spel av resultatet og last ned MP4-fila.
7. Du kan lagre ein lokal publiseringsplan etter at du har godkjent videoen, kanalen og tidspunktet.

Prøv gjerne både eit klipp med lyd og eit utan lyd. Eksporten får eit lydspor òg når ei kjelde manglar lyd.

### Korleis den enkle redigeringa verkar

Motoren tek eit utdrag frå starten av kvart kjeldeklipp, i opplastingsrekkjefølgje. Den ønskte lengda blir fordelt proporsjonalt mellom klippa. Dette er deterministisk trimming, ikkje ei vurdering av kva innhaldet handlar om. Han vel ikkje automatisk dei mest interessante augneblikka.

Stilen styrer enkle fade-inn- og fade-ut-effektar: roleg brukar inntil 0,6 sekund, profesjonell inntil 0,25 sekund, og energisk brukar harde klipp. Svært korte utdrag får tilpassa fade-lengd.

### Korleis Gemini-redigeringa verkar

På Render sender appen opptil tre tidsmerkte bilete frå kvart klipp i ei samla analyseførespurnad. Gemini brukar bileta og instruksjonen til å velje utdrag og rekkjefølgje. FFmpeg brukar tidspunkta til å hente dei valde utdraga frå originalklippa, med lyd.

Ved lokal køyring brukar appen små MP4-kopiar med lyd for opptil 60 sekund samla kjeldemateriale. Lengre prosjekt og kopiar som ikkje kan lagast innan førebuingsgrensa, brukar Gemini File API.

Serveren kontrollerer klipp-ID, klippepunkt og lengd før eksport. På Render har biletførebuinga eiga tidsgrense, og Gemini får om lag 75 sekund etter at bileta er klare. Lokale AI-jobbar har framleis om lag 45 sekund til AI-stega. Ved Gemini-feil går appen vidare med enkel klipping og merkar eksporten **Reserve · enkel redigering utan AI**.

Ved HTTP 503 under analyse ventar appen og prøver opptil to gonger til med same innhald, så lenge det er tid att. På Render går standardmodellen Gemini 3.8 Flash over til Gemini 3.5 Flash-Lite ved neste forsøk. Ei anna modellinnstilling blir brukt som vald. Ventestatus, modell og forsøksnummer blir viste på jobben.

Når planen er godkjend, lagar FFmpeg den faktiske videofila lokalt. Gemini lagar altså klippeplanen; FFmpeg produserer MP4-fila. Resultatet bør alltid sjåast gjennom før du brukar det vidare, fordi AI kan misforstå innhaldet eller velje dårlege klippepunkt.

Du kan no be om ein rangeringsvideo med plasseringar, opningstittel og korte kommentarar. Til dømes: «Ranger desse tre klippa frå best til verst etter visuelt inntrykk. Start med tittelen Rangering. Vis farga plasseringstal oppe til venstre, og fortel kort på norsk kva som er fint i kvart klipp.» Førsteplass er alltid best; retninga «best til verst» eller «verst til best» blir kontrollert før eksport. Kvar kjelde må vere med éin gong, og kvar plassering må vere unik. Appen lagrar kriteriet og ei konkret AI-grunngiving, og viser dei under den ferdige videoen. Utan eit oppgitt kriterium blir visuelt inntrykk, komposisjon og tydeleg motiv brukte som ei subjektiv vurdering.

Tittelen blir vist over starten av første klipp. Ei farga liste oppe til venstre blir utvida når kvart rangerte klipp byrjar, og den aktive plasseringa blir framheva. Dette legg ikkje ekstra sekund til eksporten. Når du ber om tale, lagar Gemini eit kort manus for kvart utdrag. Ei installert norsk Windows-stemme les manuset lokalt; bakgrunnslyden blir dempa. Det krev ingen ekstra taleteneste eller API-nøkkel. Stemmestatus blir vist i appen. For langt manus gir ei forklarande feil i staden for avkutta ord; stemma blir aldri auka meir enn til 1,25 gongar normal fart. Musikk, automatiske undertekstar og vilkårlege grafiske effektar er framleis ikkje implementerte.

For ståande kjeldeklipp kan du velje 9:16 i skjemaet for å unngå store sidekantar. Appen bevarer det valde formatet, sjølv om tekstinstruksjonen nemner eit anna. Nye redigeringsfunksjonar gjeld nye jobbar; ein tidlegare eksport blir ikkje endra automatisk.

Ved bruk av File API kallar appen SDK-et si filopplasting éin gong per kjeldeklipp. SDK-et kan dele opp overføringa. Modellen blir kalla éin gong normalt og opptil tre gonger ved HTTP 503. Ventetidene er 3 og 8 sekund med opptil eitt sekund tilfeldig tillegg. Kvote- og tilgangsfeil, ugyldige planar, tidsavbrot og transportfeil gir ingen nye analyseforsøk. SDK-et sine vanlege automatiske forsøk og vidareføring av svaret er slått av.

Ventinga på kvar Google-fil er avgrensa til maksimalt 30 statusoppslag med to sekund mellom oppslaga. Kvar fil har ei klargjeringsgrense på 120 sekund, og kvart oppslag ventar i høgst ti sekund på svar. Førespurnader under opplasting har ei standardgrense på 120 sekund. Med reserve blir desse grensene korta ned til tida som står att.

Kvart analyseforsøk får tida som står att av analysebudsjettet. Gemini 3.8 Flash brukar tenkjenivå `low`. Eventuell filopprydding og MP4-eksport kjem i tillegg.

Filreferansane blir lagra før opplasting, slik at appen kan prøve å rydde opp òg om ei opplasting blir avbroten før Google har svart.

File API-filer blir forsøkt sletta etter analysen, òg ved feil. Ved mellombelse slettefeil blir sletting prøvd maksimalt to gonger i same opprydding. Når sletting ikkje kan stadfestast, blir filreferansane bevarte for opprydding ved neste oppstart eller AI-jobb. Google har dessutan ei utløpstid på 48 timar for File API-filer. Direkte analysekopiar opprettar ingen File API-filer.

Ein gammal filreferanse kan gi 403 sjølv om fila aldri vart oppretta, er borte eller høyrer til eit anna Google-prosjekt. Dersom ei fullført filliste for den gjeldande nøkkelen ikkje inneheld referansen, kan nye jobbar bruke File API. Appen bevarer referansen og viser ei åtvaring. Uavklarte filer blokkerer vidare bruk av File API. På Render kan bileteanalysen halde fram medan gamle referansar ventar på opprydding.

Instruksjonen og modellnamnet blir lagra saman med kvar jobb når han blir oppretta. Den validerte klippeplanen blir òg lagra, med klipp-ID-ar, start- og sluttider og rekkjefølgje. Seinare endringar i prosjektinnstillingar eller `GEMINI_MODEL` endrar dermed ikkje kva ein allereie oppretta jobb skulle bruke.

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
| Klipp i ein Gemini-jobb | 10, ei grense sett av denne appen |
| Lengd per kjeldeklipp | 30 minutt |
| Samla kjeldelengd i ein Gemini-jobb | 30 minutt, ei grense sett av denne appen |
| Ønskt eksportlengd | 1 sekund–10 minutt |
| Lokal lagringsgrense | 2 GiB |

Ønskt eksportlengd må vere innanfor den samla lengda til kjeldene. Appen skal forklare når materialet er for kort; han strekkjer eller repeterer ikkje klippa for å fylle tida. Lengda kan avvike litt på grunn av avrunding til heile videobilete.

## Testar

Testane brukar ei mellombels datamappe og små, syntetiske videofiler. Dei krev at FFmpeg og ffprobe er installerte. Køyr frå prosjektroten:

Gemini-testane dekkjer planvalidering, samtykke, direkte analysekopiar, filopprydding og overgang til ein nedlastbar reservevideo etter eit tidsavbrot. Dei brukar lokale testtransportar og syntetiske videoar.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

På macOS/Linux:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Testane er laga for å kontrollere opplasting, verkeleg videoeksport og dekoding, format og lengd, ugyldige førespurnader, lagring og lokale publiseringsplanar. Sjå testutskrifta for kva som faktisk vart køyrt og bestod på di maskin. Ein manuell nettlesartest bør òg kontrollere avspeling, nedlasting, mobiloppsett og tastaturnavigasjon.

Gemini-testar skal bruke simulerte SDK-svar og ikkje sende videoar til ein betalt API. Dei kan kontrollere planvalidering, feilhandsaming, avgrensa polling og filopprydding, men dei beviser ikkje kvaliteten eller tilgangen til ein verkeleg modell. Ingen levande Gemini-analyse er køyrd som del av denne oppgåva.

For ein valfri manuell test på di eiga maskin: konfigurer nøkkelen, vel eitt kort klipp utan sensitivt innhald, slå på Gemini-modus og start jobben sjølv. Kontroller at analysefasen blir vist, at resultatet kan spelast av, og at feil ikkje blir merkte som vellukka AI. Denne testen kan medføre API-kostnader.

Før Gemini-integrasjonen vart lagd til, vart åtte integrasjonstestar køyrde i Linux-skymiljøet med Python 3.12 og FFmpeg 7.1. Dei omfattar alle tre format, kjelder med og utan lyd, ein 61-sekunds eksport, ugyldige filer, feila jobbar og gjenoppretting etter omstart. Chromium vart òg brukt til heile flyten frå prosjektoppretting til avspeling, nedlasting og godkjend lokal plan. Mobiloppsettet vart kontrollert ned til 320 piksel. Windows-kommandoane over er skrivne for PowerShell, men er ikkje køyrde på ein Windows-maskin her.

## Struktur og viktige val

```text
Ai-proskjekt/
├── app/
│   ├── main.py          # API, SQLite og kø for bakgrunnsjobbar
│   ├── gemini.py        # Gemini-SDK, analyse og strukturert klippeplan
│   ├── cut_plan.py      # Streng validering av AI-utdata mot lokale klipp
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
- **Gemini-adapteren** er skild frå FFmpeg-motoren. Han brukar berre det offisielle SDK-et og får ikkje lov til å bestemme lokale filstiar eller kommandoar. Enkel modus er tilgjengeleg utan SDK-kall.
- **Filkontroll** omfattar både innstillingar og faktisk medieinnhald. Opplasta filer får genererte interne namn. FFmpeg blir starta med argumentlister utan shell, slik at brukartekst ikkje blir ein kommando. Mellombelse renderfiler blir rydda opp etter jobben.
- **Lokale data** og `.env` er ignorerte av Git. Ikkje legg inn opplasta videoar, databasen eller API-nøklar manuelt. Sletting av `data` fjernar lokale prosjekt og videoar; ta vare på filer du treng først.

## Demonstrasjon og vidare arbeid

Abonnementssida viser døme på moglege planar og prisar. Det finst inga aktiv betaling, betalingsintegrasjon eller abonnementsinnkrevjing. Brukstala er lokale, og er ikkje eit ferdig system for månadsfakturering.

Publiseringskalenderen lagrar berre planar. Kanalnamn som YouTube, Instagram eller TikTok betyr ikkje at kontoen er tilkopla. Ingen video blir send til ei plattform. Tidspunkt blir lagra med tidssoneinformasjon, og identiske planleggingsforsøk skal ikkje opprette doble planar.

Naturlege neste steg er:

1. Prøve Gemini-kvaliteten med eigne godkjende testvideoar og betre instruksjonar og klippeplanar. Valfri teksting krev framleis ei eiga transkripsjonsløysing.
2. Innlogging og tilgangskontroll, slik at kvar brukar berre får tilgang til sine eigne prosjekt og filer.
3. Ekte abonnementsgrenser og betalingsintegrasjon i testmodus.
4. Offisielle API-ar og autorisert kontotilkopling for publisering, basert på oppdatert dokumentasjon for kvar plattform.

Tenesta må få meir tilgangskontroll, driftsoppsett og ressursstyring før ho kan vere ei offentleg fleirbrukarteneste. Denne prototypen skal starte på `127.0.0.1`, ikkje gjerast offentleg tilgjengeleg.

## Kjelder for Gemini-integrasjonen

Følgjande offisielle Google-kjelder vart konsulterte 30. oktober 2026:

- [Google GenAI Python SDK](https://github.com/googleapis/python-genai/blob/main/README.md): klientoppsett, miljøvariablar, strukturert JSON og filopplasting/sletting.
- [SDK-typane](https://github.com/googleapis/python-genai/blob/main/google/genai/types.py): tidsavbrot i millisekund, gjentakingsval og filtilstandar.
- [SDK-klienten](https://github.com/googleapis/python-genai/blob/main/google/genai/_api_client.py): intern handtering av resumable upload og overføringsdelar.
- [Video understanding](https://github.com/google-gemini/cookbook/blob/main/quickstarts/Video_understanding.ipynb): videoopplasting, behandling og analyse. Dømet brukar `gemini-3.8-flash` og viser òg `models.generate_content` for video.
- [JSON mode](https://github.com/google-gemini/cookbook/blob/main/quickstarts/JSON_mode.ipynb): strukturert utdata og modellval.
- [Get started](https://github.com/google-gemini/cookbook/blob/main/quickstarts/Get_started.ipynb): tilrår `gemini-3.8-flash` som standardmodell i dømet.
- [File API](https://github.com/google-gemini/cookbook/blob/main/quickstarts/File_API.ipynb): filtilstandar og automatisk utløp etter 48 timar.
- [google-genai på PyPI](https://pypi.org/project/google-genai/): SDK-versjon 2.34.0 og Python-krav.

Direkte tilgang til dokumentasjon på `ai.google.dev` var blokkert av nettproxyen i skymiljøet. Implementasjonen byggjer derfor på dei offentlege, offisielle Google-repositorya og SDK-kjelda ovanfor. Modelltilgang, prisar og kvotar må kontrollerast for Google-kontoen som skal bruke integrasjonen.
