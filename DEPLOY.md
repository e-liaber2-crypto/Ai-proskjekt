# Gratis lærardemo på Render

Dette er ein delt, passordverna demonstrasjon for elev og lærar. Begge ser dei same prosjekta. Lokal bruk fungerer som før.

1. Opprett gratis konto på https://dashboard.render.com med GitHub.
2. Vel **New → Blueprint**, og kople til repositoryet `e-liaber2-crypto/Ai-proskjekt`.
3. Render les `render.yaml`. Kontroller at planen er **Free**, og start deploy.
4. Under tenesta → **Environment** finn du den genererte `DEMO_PASSWORD`. Brukarnamnet er `laerar`. Del passordet privat med læraren.
5. Opne HTTPS-lenkja Render viser, logg inn, last opp eit kort testklipp og prøv ein eksport før du sender lenkja til læraren.

Render set `RENDER_EXTERNAL_HOSTNAME` automatisk. Appen krev passord på minst 16 teikn når eit offentleg vertsnamn er sett. På andre tenester må `PUBLIC_HOST` setjast til det eksakte vertsnamnet utan `https://` eller sti. Bruk HTTPS. `/healthz` er ein offentleg helsesjekk utan prosjektdata.

## Avgrensingar

- Gratistenesta sovnar etter 15 minutt utan trafikk og brukar om lag eitt minutt på å vakne. Prosjekt, videoar og database blir sletta ved dvale, omstart og ny deploy. Last ned ferdige videoar straks.
- Gratisressursane er avgrensa. Prøv korte, små klipp først. Faktisk videoeksport må verifiserast på serveren.
- Windows-forteljarstemma er ikkje tilgjengeleg på Linux. Vanleg klipping, titlar og rangeringsgrafikk kan køyrast med FFmpeg.
- Gemini er valfritt. Demoen legg ikkje inn API-nøkkelen din. Enkel redigering fungerer utan nøkkel. Gemini krev separat nøkkel og kvote, og er ikkje garantert gratis.
- Ikkje legg til betalingsmåte eller oppgrader plan dersom demonstrasjonen skal vere gratis. Gratisgrenser kan stanse tenesta.
- Ikkje last opp sensitive videoar i denne delte skule-demoen.

Dokumentasjon: https://render.com/docs/free og https://render.com/docs/infrastructure-as-code
