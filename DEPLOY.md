# Gratis lærardemo på Render

Dette er ein delt, passordverna demonstrasjon for elev og lærar. Begge ser dei same prosjekta. Lokal bruk fungerer som før.

1. Opprett gratis konto på https://dashboard.render.com med GitHub.
2. Vel **New → Blueprint**, og kople til repositoryet `e-liaber2-crypto/Ai-proskjekt`.
3. Render les `render.yaml`. Kontroller at planen er **Free**, og start deploy.
4. Under tenesta → **Environment** finn du den genererte `DEMO_PASSWORD`. Brukarnamnet er `laerar`. Del passordet privat med læraren.
5. Opne HTTPS-lenkja Render viser, logg inn, last opp eit kort testklipp og prøv ein eksport før du sender lenkja til læraren.

Render set `RENDER_EXTERNAL_HOSTNAME` automatisk. Appen krev passord på minst 16 teikn når eit offentleg vertsnamn er sett. På andre tenester må `PUBLIC_HOST` setjast til det eksakte vertsnamnet utan `https://` eller sti. Bruk HTTPS. `/healthz` er ein offentleg helsesjekk utan prosjektdata.

## Når prosjektet allereie ligg på Render

1. Hent endringane til `main` i GitHub. Dersom Render brukar ein annan branch, vel den rette under **Settings → Branch**.
2. Synkroniser Blueprint-en slik at `render.yaml` blir brukt. Ved ei manuelt oppretta Web Service må **Runtime** vere **Docker**, **Health Check Path** vere `/healthz`, og **Auto-Deploy** vere **After CI Checks Pass**.
3. Kontroller at `DEMO_PASSWORD` finst og har minst 16 teikn. Behald det eksisterande passordet. Appen brukar brukarnamnet `laerar`.
4. Gemini er valfritt: legg `GEMINI_API_KEY` inn under **Environment** på Render dersom du vil bruke AI. Nøkkelen blir ikkje henta frå `.env` på PC-en din og skal aldri inn i GitHub. `GEMINI_MODEL` er sett i Blueprint-en og kan endrast til ein modell du har tilgang til.
5. La **Docker Command** stå tom slik at startkommandoen i Dockerfile blir brukt. Han lyttar på Render sin `$PORT` og køyrer éin API-prosess med éin videoarbeidar.
6. Vent til begge GitHub-kontrollane er grøne. **Live** på Render betyr at oppstarten og helsesjekken fungerer; prøv òg eit kort klipp og last ned resultatet for å kontrollere heile flyten på tenesta.

`/healthz` testar databasen, arbeidstråden og at FFmpeg/ffprobe finst. API-nøklar og prosjektdata blir ikkje viste der. Ved feil returnerer han `503`, slik at Render oppdagar ei teneste som ikkje kan behandle video.

## Kontroller før du pushar

Frå prosjektmappa, med avhengigheitene installerte:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check app.js
node --test tests/api.test.cjs tests/result_ui.test.cjs
```

GitHub Actions køyrer dei same testane og byggjer Docker-image-en. Ein eigen oppstartstest sjekkar at containeren brukar rett port, krev passord og rapporterer at videomotoren er tilgjengeleg. AI-testane bruker lokale testtransportar og brukar ingen Google-kvote. Windows-stemmetestar blir hoppa over på Linux.

## Lagring og ressursar

`DATA_DIR=/app/data` er mellombels lagring på Free. Nettsida viser dette tydeleg. `FFMPEG_THREADS=1` avgrensar talet på FFmpeg-trådar; dette er ikkje ein garanti for at store videoar passar i minnet eller blir ferdige raskt på Free.

Dersom du seinare vel ein betalt plan med vedvarande disk, monter disken, peik `DATA_DIR` på den monterte mappa, og set fyrst då `PERSISTENT_STORAGE=true`. Den innstillinga lagar ikkje ein disk og blir ikkje aktivert i gratisoppsettet.

## Avgrensingar

- Gratistenesta sovnar etter 15 minutt utan trafikk og brukar om lag eitt minutt på å vakne. Prosjekt, videoar og database blir sletta ved dvale, omstart og ny deploy. Last ned ferdige videoar straks.
- Gratisressursane er avgrensa. Prøv korte, små klipp først. Faktisk videoeksport må verifiserast på serveren.
- Windows-forteljarstemma er ikkje tilgjengeleg på Linux. Vanleg klipping, titlar og rangeringsgrafikk kan køyrast med FFmpeg.
- Gemini er valfritt. Demoen legg ikkje inn API-nøkkelen din. Enkel redigering fungerer utan nøkkel. Gemini krev separat nøkkel og kvote, og er ikkje garantert gratis.
- Ikkje legg til betalingsmåte eller oppgrader plan dersom demonstrasjonen skal vere gratis. Gratisgrenser kan stanse tenesta.
- Ikkje last opp sensitive videoar i denne delte skule-demoen.

Dokumentasjon: https://render.com/docs/free og https://render.com/docs/infrastructure-as-code
