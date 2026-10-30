# AI-videoredigering – skuleprosjekt

Ein lokal prototype for eit framtidig abonnementsbasert videoverktøy, laga med ein enkel struktur som passar eit VG2 IT-prosjekt. Du kan opprette prosjekt, laste opp videoklipp, velje innstillingar og eksportere ein ekte MP4-video med FFmpeg. Med ei konfigurert Gemini-kopling kan Google analysere videoane og foreslå kva utdrag som skal brukast, i kva rekkjefølgje.

**Denne versjonen er ein lokal demonstrasjon utan innlogging.** Han skal berre køyrast på di eiga maskin. Enkel redigering fungerer utan API-nøkkel; Gemini-analyse er valfri og sender kjeldeklippa til Google. Betaling og automatisk publisering er ikkje aktiverte.

## Kva fungerer?

- Prosjektoversikt og arbeidsflate med lagring i SQLite.
- Opplasting av fleire videoklipp og kontroll av filene på serveren.
- Ønskt lengd i minutt og sekund, også for videoar som varer fleire minutt.
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

Gemini-analyse kan koste pengar og lastar videoklippa opp til Google. Bruk berre klipp du har rett til å sende dit, og kontroller dei gjeldande vilkåra, prisane og kvotane for kontoen din. Vanleg opplasting til prosjektet er lokal; Google får berre klippa når du uttrykkeleg startar ein AI-jobb og godkjenner sendinga. Samtykke må givast for kvar ny AI-jobb. Å opne nettsida startar ikkje analyse. Undertekstar er framleis ikkje implementerte; ein Gemini-nøkkel aktiverer ikkje transkripsjon eller teksting.

## Slik prøver du hovudflyten

1. Opprett eit prosjekt med eit namn.
2. Last opp eitt eller fleire små videoklipp.
3. Vel enkel redigering utan AI, eller Gemini-analyse dersom ho er konfigurert. Skriv kva du ønskjer å lage; teksten blir berre tolka i Gemini-modus.
4. Vel lengd, format, kantar eller beskjering og redigeringsstil. I Gemini-modus må du òg godkjenne at kjeldeklippa blir sende til Google.
5. Start behandling. Statusen kjem frå den faktiske bakgrunnsjobben.
6. Spel av resultatet og last ned MP4-fila.
7. Du kan lagre ein lokal publiseringsplan etter at du har godkjent videoen, kanalen og tidspunktet.

Prøv gjerne både eit klipp med lyd og eit utan lyd. Eksporten får eit lydspor òg når ei kjelde manglar lyd.

### Korleis den enkle redigeringa verkar

Motoren tek eit utdrag frå starten av kvart kjeldeklipp, i opplastingsrekkjefølgje. Den ønskte lengda blir fordelt proporsjonalt mellom klippa. Dette er deterministisk trimming, ikkje ei vurdering av kva innhaldet handlar om. Han vel ikkje automatisk dei mest interessante augneblikka.

Stilen styrer enkle fade-inn- og fade-ut-effektar: roleg brukar inntil 0,6 sekund, profesjonell inntil 0,25 sekund, og energisk brukar harde klipp. Svært korte utdrag får tilpassa fade-lengd.

### Korleis Gemini-redigeringa verkar

Bakgrunnsarbeidaren lastar opp kjeldeklippa gjennom Gemini File API og ventar til Google har behandla filene. Deretter sender han instruksjonane dine, videoane og ønskte innstillingar til den valde modellen. Modellen skal returnere strukturert JSON med klipp-ID, starttid, sluttid og rekkjefølgje.

Serveren kontrollerer planen før eksport: klipp-ID-ane må finnast i prosjektet, tider må vere endelege og innanfor kjeldeklippa, og planen må halde seg innanfor ønskte lengde- og ressursgrenser. Modellen kan ikkje sende filstiar eller shell-kommandoar til FFmpeg. Ugyldige svar, tryggleiksblokkeringar, kvotefeil og nettverksfeil skal gi ein forståeleg jobbfeil. Ein AI-feil blir ikkje presentert som eit vellukka AI-resultat eller automatisk erstatta med enkel redigering.

Når planen er godkjend, lagar FFmpeg den faktiske videofila lokalt. Gemini lagar altså klippeplanen; FFmpeg produserer MP4-fila. Resultatet bør alltid sjåast gjennom før du brukar det vidare, fordi AI kan misforstå innhaldet eller velje dårlege klippepunkt.

Appen kallar SDK-et si filopplasting éin gong per kjeldeklipp og modellanalyse éin gong per jobb. Vanlege API-retries er slått av med `HttpRetryOptions(attempts=1)`, og `automatic_continuation=False` hindrar automatisk vidareføring av modellsvaret. Dette betyr ikkje at ei heil fil alltid blir send med berre éin HTTP-førespurnad: SDK-et brukar resumable upload og kan internt prøve ein overføringsdel opptil tre gonger dersom svaret manglar opplastingsstatus. Appen startar ikkje ein ny analyse for å handtere dette.

Ventinga på kvar Google-fil er avgrensa til maksimalt 30 statusoppslag, med to sekund mellom oppslaga og ei fristkontroll på 120 sekund. Eit statusoppslag har ti sekund tidsavbrot; eit allereie starta oppslag kan fullførast etter fristen. Analyseførespurnaden har 60 sekund tidsavbrot, og førespurnader under opplasting har 120 sekund. Filreferansane blir lagra før opplasting, slik at appen kan prøve å rydde opp òg om ei opplasting blir avbroten før Google har svart.

Opplasta Gemini-filer blir forsøkt sletta etter analyse, også når jobben feilar. Ved mellombelse slettefeil blir sletting prøvd maksimalt to gonger i same opprydding. Dersom slettinga ikkje kan stadfestast, blir jobben merkt mislukka med ei forståeleg melding, og filreferansane blir bevarte. Start serveren på nytt for å prøve opprydding igjen; restar kan òg ryddast før neste jobb, utan ein ny analyse. Google dokumenterer dessutan at File API-filer automatisk går ut etter 48 timar. Dette erstattar ikkje appen sine forsøk på å slette filene. Dei lokale kjeldeklippa blir verande i prosjektet til du slettar dei.

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

Gemini-versjonen er kontrollert med **27 beståtte testar** i Linux-skymiljøet. Dei nye testane dekkjer planvalidering, samtykke, kvotefeil, tidsavbrot, avgrensa forsøk, inga basic-fallback og opprydding etter omstart. Ein test brukar det verkelege Google-SDK-et med ein lokal, mocka HTTP-transport for opplasting, analyse og sletting. Nettverkskoplingar er sperra i Gemini-testane; ingen reell nøkkel, Google-analyse eller betalte API-kall er brukte. Gemini-tilgang og kvaliteten på faktiske modellval må prøvast lokalt når du sjølv startar ein AI-jobb.

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
