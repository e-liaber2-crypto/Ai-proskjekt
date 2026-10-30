"""Videoanalyse med Google sitt offisielle SDK. Ingen automatisk basic-fallback.

Nøklar blir berre brukte av serveren ved klientoppretting, aldri serialiserte.
Kvar fil blir lasta opp éin gong og modellen blir kalla éin gong per jobb.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time
from typing import Callable
from uuid import uuid4

from google import genai
from google.genai import types
import httpx

from . import media
from .cut_plan import CutPlan, parse_plan

DEFAULT_MODEL = "gemini-3.8-flash"
MAX_AI_FILES = 10  # Appen si eiga grense, ikkje ein lovnad om Google-kvote.
MAX_AI_SOURCE_SECONDS = 1800
MAX_FILE_POLLS = 30
POLL_INTERVAL_SECONDS = 2
FILE_READY_TIMEOUT_SECONDS = 120
MIME_TYPES = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm", ".mkv": "video/x-matroska"}
FILE_NAME = re.compile(r"files/[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$|files/[a-z0-9]$")


class GeminiError(media.MediaError):
    """Sanert feilmelding. SDK-feiltekst kan innehalde sensitive førespurnader."""

    def __init__(self, message: str, code: str = "provider"):
        super().__init__(message)
        self.code = code


def configured() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


def model_name() -> str:
    return os.environ.get("GEMINI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _http_options(timeout: int = 60_000) -> types.HttpOptions:
    return types.HttpOptions(
        base_url="https://generativelanguage.googleapis.com",
        api_version="v1beta",
        timeout=timeout,
        retry_options=types.HttpRetryOptions(attempts=1),
    )


def _client():
    if not configured():
        raise GeminiError("GEMINI_API_KEY manglar i servermiljøet. Set nøkkelen lokalt og start serveren på nytt, eller vel enkel redigering.", "missing_key")
    # Eksplisitt val hindrar at GOOGLE_API_KEY eller Vertex-innstillingar overstyrer.
    return genai.Client(api_key=os.environ.get("GEMINI_API_KEY"), vertexai=False, http_options=_http_options())


def _code(error: Exception) -> int | None:
    value = getattr(error, "code", None)
    return value if isinstance(value, int) else None


def _safe_error(error: Exception) -> GeminiError:
    status = _code(error)
    if status == 429:
        return GeminiError("Google si kvote eller fartsgrense er nådd. Kontroller kvoten i Google AI Studio og prøv seinare. Ingen ny analyse blir starta automatisk.", "quota")
    if status in (401, 403):
        return GeminiError("Google avviste tilgangen. Kontroller GEMINI_API_KEY og modelltilgangen i Google AI Studio, og start serveren på nytt.", "access")
    if status == 404:
        return GeminiError("Google fann ikkje modellen eller ei av videofilene. Kontroller GEMINI_MODEL og at modellen støttar videoanalyse.", "not_found")
    if status in (408, 504) or isinstance(error, (httpx.TimeoutException, TimeoutError)):
        return GeminiError("Google-analysen nådde tidsgrensa. Jobben er stoppa; han blir ikkje sendt til analyse på nytt automatisk.", "timeout")
    if status is not None and status >= 500:
        return GeminiError("Google-tenesta er mellombels utilgjengeleg. Prøv igjen seinare. Jobben er ikkje bytt til enkel redigering.", "unavailable")
    if isinstance(error, httpx.TransportError):
        return GeminiError("Fekk ikkje kontakt med Google. Kontroller nettverk og tilgang til generativelanguage.googleapis.com.", "network")
    if status == 400:
        return GeminiError("Google avviste analyseførespurnaden. Kontroller at GEMINI_MODEL støttar video og strukturerte svar, og at videoformatet er støtta.", "request")
    return GeminiError("Gemini-analysen kunne ikkje fullførast. Kontroller modell, tilgang og videofiler. Ingen ny analyse blir prøvd automatisk.", "provider")


def _transient(error: Exception) -> bool:
    return _code(error) in (408, 429, 500, 502, 503, 504) or isinstance(error, (httpx.TransportError, TimeoutError))


def _delete_files(client, names: list[str], on_deleted: Callable[[str], None] | None) -> list[str]:
    remaining = []
    for name in dict.fromkeys(names):
        if not FILE_NAME.fullmatch(name):
            remaining.append(name)
            continue
        removed = False
        for attempt in range(2):
            try:
                client.files.delete(name=name, config=types.DeleteFileConfig(http_options=_http_options(10_000)))
                removed = True
            except Exception as error:
                if _code(error) == 404:
                    removed = True
                elif attempt == 0 and _transient(error):
                    time.sleep(1)
                    continue
            break
        if removed:
            try:
                if on_deleted:
                    on_deleted(name)
            except Exception:
                remaining.append(name)
        else:
            remaining.append(name)
    return remaining


def cleanup_files(names: list[str], on_deleted: Callable[[str], None] | None = None) -> list[str]:
    """Prøv berre sletting av tidlegare eigne filer, aldri opplasting eller analyse."""
    if not names or not configured():
        return list(names)
    client = None
    try:
        client = _client()
        return _delete_files(client, names, on_deleted)
    except Exception:
        return list(names)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def _wait_for_file(client, uploaded):
    deadline = time.monotonic() + FILE_READY_TIMEOUT_SECONDS
    current = uploaded
    for attempt in range(MAX_FILE_POLLS + 1):
        state = getattr(current.state, "name", current.state)
        if state == "ACTIVE":
            return current
        if state == "FAILED":
            raise GeminiError("Google kunne ikkje klargjere eit av videoklippa. Prøv eit anna klipp eller eit anna videoformat.", "file_failed")
        if state != "PROCESSING":
            raise GeminiError("Google returnerte ein ukjend filstatus. Analysen er stoppa.", "file_state")
        if attempt == MAX_FILE_POLLS or time.monotonic() >= deadline:
            raise GeminiError("Google brukte for lang tid på å klargjere videoen. Analysen er stoppa utan ny opplasting.", "file_timeout")
        time.sleep(POLL_INTERVAL_SECONDS)
        current = client.files.get(name=uploaded.name, config=types.GetFileConfig(http_options=_http_options(10_000)))
    raise GeminiError("Videoen vart ikkje klar innan tidsgrensa.", "file_timeout")


def generate_plan(
    clips: list[dict],
    description: str,
    duration_seconds: int,
    style: str,
    progress: Callable[[int, str], None],
    *,
    on_uploaded: Callable[[str], None] | None = None,
    on_deleted: Callable[[str], None] | None = None,
    model: str | None = None,
) -> list[dict]:
    """Send klippa til Google, valider svaret og slett leverandørfilene i finally."""
    if not 1 <= len(clips) <= MAX_AI_FILES:
        raise GeminiError("AI-modus støttar mellom eitt og ti videoklipp per jobb.", "limits")
    if sum(clip["duration"] for clip in clips) > MAX_AI_SOURCE_SECONDS:
        raise GeminiError("AI-modus støttar høgst 30 minutt samla kjeldemateriale per jobb.", "limits")
    if not description.strip():
        raise GeminiError("Skriv kva du ønskjer at Gemini skal lage før du startar AI-redigering.", "instruction")
    # Same førehandskontroll som enkel modus, utan å sende noko til Google.
    media.build_plan(clips, duration_seconds, style)
    client = None
    remote_names = []
    failure = None
    plan = None
    try:
        client = _client()
        contents = []
        for index, clip in enumerate(clips):
            progress(3 + int(20 * index / len(clips)), f"Sender klipp {index + 1} av {len(clips)} til Google")
            path = Path(clip["path"])
            mime = MIME_TYPES.get(path.suffix.lower())
            if not mime:
                raise GeminiError("Videoformatet kan ikkje sendast til Gemini. Bruk MP4, MOV, WebM eller MKV.", "file_type")
            # Google støttar eit førehandsvalt filnamn. Journalfør FØR nettverkskallet:
            # då kan vi slette fila òg om upload-svaret aldri kjem fram.
            reserved_name = "files/" + uuid4().hex
            if on_uploaded:
                on_uploaded(reserved_name)
            remote_names.append(reserved_name)
            uploaded = client.files.upload(
                file=path,
                config=types.UploadFileConfig(name=reserved_name, mime_type=mime, http_options=_http_options(120_000)),
            )
            if uploaded.name != reserved_name:
                if isinstance(uploaded.name, str) and FILE_NAME.fullmatch(uploaded.name):
                    remote_names.append(uploaded.name)
                    if on_uploaded:
                        on_uploaded(uploaded.name)
                raise GeminiError("Google returnerte ein uventa filreferanse. Analysen er stoppa.", "file_reference")
            progress(4 + int(20 * index / len(clips)), f"Google klargjer klipp {index + 1} av {len(clips)}")
            ready = _wait_for_file(client, uploaded)
            if not ready.uri or not ready.mime_type:
                raise GeminiError("Google returnerte ufullstendige videometadata.", "file_reference")
            # ID og videoreferanse blir para tydeleg. Lokale stiar/filnamn blir ikkje sende.
            contents.extend([
                types.Part.from_text(text=json.dumps({"clip_id": clip["id"], "duration_seconds": clip["duration"]})),
                types.Part.from_uri(file_uri=ready.uri, mime_type=ready.mime_type),
            ])
        instruction = {
            "instruction": description,
            "target_duration_seconds": duration_seconds,
            "style": style,
            "rules": [
                "Analyse all attached video clips and select relevant content matching the instruction.",
                "Return ONLY the structured cut plan. No invented clips, URLs, file paths, or commentary.",
                "Use numeric seconds relative to the beginning of each attached clip.",
                "Use between 1 and 12 segments, each at least 1/24 second long.",
                "Use order values 1..N exactly once. You may reorder clips and use multiple non-overlapping excerpts.",
                "All start/end times must be within the supplied clip duration. Do not repeat overlapping source ranges.",
                "Sum of end_seconds minus start_seconds MUST equal target_duration_seconds within 0.002 seconds.",
            ],
        }
        contents.append(types.Part.from_text(text=json.dumps(instruction, ensure_ascii=False)))
        progress(28, "Gemini analyserer videoklippa og lagar ein klippeplan")
        response = client.models.generate_content(
            model=model or model_name(),
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=CutPlan.model_json_schema(),
                temperature=0.2,
                max_output_tokens=4096,
                automatic_continuation=False,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )
        progress(40, "Validerer klippeplanen frå Gemini")
        try:
            plan = parse_plan(response.text, clips, duration_seconds)
        except media.MediaError:
            raise GeminiError("Gemini returnerte ein ugyldig klippeplan. Ingen video er eksportert. Endre instruksjonen eller prøv ein annan videomodell.", "invalid_plan") from None
        progress(42, "Ryddar mellombelse videofiler hos Google")
    except GeminiError as error:
        failure = error
    except Exception as error:
        failure = _safe_error(error)
    finally:
        remaining = _delete_files(client, remote_names, on_deleted) if client is not None else remote_names
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
    if remaining:
        message = "Mellombelse Google-filer kunne ikkje slettast. Filreferansane er lagra for ny opprydding ved neste oppstart eller jobb. Ingen ny analyse blir starta av oppryddinga."
        if failure:
            message = str(failure) + " " + message
        raise GeminiError(message, "cleanup") from None
    if failure:
        raise failure from None
    return plan
