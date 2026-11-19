"""Videoanalyse med Google sitt offisielle SDK og validering av klippeplanen."""

from __future__ import annotations

import json
import os
from pathlib import Path
from random import uniform
import re
from tempfile import TemporaryDirectory
import time
from typing import Callable
from uuid import uuid4

from google import genai
from google.genai import types
import httpx

from . import media
from .cut_plan import CutPlan, parse_plan

DEFAULT_MODEL = "gemini-3.8-flash"
RENDER_RESERVE_MODEL = "gemini-3.5-flash-lite"
MAX_AI_FILES = 10  # Appen si eiga grense, ikkje ein lovnad om Google-kvote.
MAX_AI_SOURCE_SECONDS = 1800
MAX_FILE_POLLS = 30
POLL_INTERVAL_SECONDS = 2
FILE_READY_TIMEOUT_SECONDS = 120
ANALYSIS_TIMEOUT_MS = 180_000
INLINE_SOURCE_SECONDS = 60
INLINE_MAX_BYTES = 4 * 1024 * 1024
PREPARATION_TIMEOUT_SECONDS = 35
ANALYSIS_FRAME_BUDGET = 48
RESERVE_WAIT_SECONDS = 45
RENDER_FRAME_SECONDS = 40
RENDER_ANALYSIS_SECONDS = 75
RENDER_FRAME_MAX_BYTES = 2 * 1024 * 1024
CLEANUP_WAIT_SECONDS = 10
ANALYSIS_RETRY_DELAYS = (3, 8)
MAX_ANALYSIS_ATTEMPTS = len(ANALYSIS_RETRY_DELAYS) + 1
MIME_TYPES = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm", ".mkv": "video/x-matroska"}
FILE_NAME = re.compile(r"files/[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$|files/[a-z0-9]$")
OPERATIONS = {"upload": "opplasting", "file_processing": "klargjering av video", "analysis": "videoanalyse"}


class GeminiError(media.MediaError):
    """Sanert feilmelding. SDK-feiltekst kan innehalde sensitive førespurnader."""

    def __init__(self, message: str, code: str = "provider", *,
                 http_status: int | None = None, operation: str | None = None):
        super().__init__(message)
        self.code = code
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        self.operation = operation if operation in OPERATIONS else None


def configured() -> bool:
    return configuration_error() is None


def configuration_error() -> str | None:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return "GEMINI_API_KEY manglar i servermiljøet. Set nøkkelen i Environment på Render eller i lokal .env og start serveren på nytt, eller vel enkel redigering."
    if not key.isascii() or any(ord(char) <= 32 or ord(char) == 127 for char in key):
        return "GEMINI_API_KEY har ugyldige teikn eller mellomrom. Kopier den originale API-nøkkelen til lokal .env og start serveren på nytt. Ikkje del nøkkelen i chat eller Git."
    return None


def model_name() -> str:
    return os.environ.get("GEMINI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _http_options(timeout: int = 60_000) -> types.HttpOptions:
    return types.HttpOptions(
        base_url="https://generativelanguage.googleapis.com",
        api_version="v1beta",
        timeout=timeout,
        retry_options=types.HttpRetryOptions(attempts=1),
    )


def _remaining_ms(deadline: float | None, limit_ms: int) -> int:
    if deadline is None:
        return limit_ms
    remaining = int((deadline - time.monotonic()) * 1000)
    if remaining < 1:
        raise TimeoutError()
    return min(remaining, limit_ms)


def _client():
    error = configuration_error()
    if error:
        raise GeminiError(error, "invalid_key" if os.environ.get("GEMINI_API_KEY") else "missing_key")
    # Eksplisitt val hindrar at GOOGLE_API_KEY eller Vertex-innstillingar overstyrer.
    return genai.Client(api_key=os.environ.get("GEMINI_API_KEY"), vertexai=False, http_options=_http_options())


def _code(error: Exception) -> int | None:
    value = getattr(error, "code", None)
    return value if type(value) is int and 100 <= value <= 599 else None


def _safe_error(error: Exception, *, operation: str | None = None) -> GeminiError:
    status = _code(error)

    def failure(message: str, code: str) -> GeminiError:
        return GeminiError(message, code, http_status=status, operation=operation)

    if status == 429:
        return failure("Google si kvote eller fartsgrense er nådd. Kontroller kvoten i Google AI Studio og prøv seinare. Ingen ny analyse blir starta automatisk.", "quota")
    if status in (401, 403):
        return failure("Google avviste tilgangen. Kontroller GEMINI_API_KEY og modelltilgangen i Google AI Studio, og start serveren på nytt.", "access")
    if status == 404:
        return failure("Google fann ikkje modellen eller ei av videofilene. Kontroller GEMINI_MODEL og at modellen støttar videoanalyse.", "not_found")
    if status in (408, 504) or isinstance(error, (httpx.TimeoutException, TimeoutError)):
        step = OPERATIONS.get(operation, "videoanalysen")
        return failure(f"Tidsgrensa vart nådd under {step} hos Google. Prøv med færre eller kortare klipp, eller vel enkel redigering utan AI. Ingen ny analyse blir starta automatisk.", "timeout")
    if status is not None and status >= 500:
        reason = {
            500: "Google fekk ein intern serverfeil",
            503: "Google-tenesta er overbelasta eller mellombels utilgjengeleg",
        }.get(status, "Google returnerte ein serverfeil")
        step = f" under {OPERATIONS[operation]}" if operation in OPERATIONS else ""
        return failure(f"{reason}{step} (HTTP {status}). Vent litt før du startar ein ny jobb. "
                       "Ingen ny analyse blir starta automatisk.", "unavailable")
    if isinstance(error, httpx.TransportError):
        return failure("Fekk ikkje kontakt med Google. Kontroller nettverk og tilgang til generativelanguage.googleapis.com.", "network")
    if status == 400:
        return failure("Google avviste analyseførespurnaden. Kontroller at GEMINI_MODEL støttar video og strukturerte svar, og at videoformatet er støtta.", "request")
    return failure("Gemini-analysen kunne ikkje fullførast. Kontroller modell, tilgang og videofiler. Ingen ny analyse blir prøvd automatisk.", "provider")


def _transient(error: Exception) -> bool:
    return _code(error) in (408, 429, 500, 502, 503, 504) or isinstance(error, (httpx.TransportError, TimeoutError))


def _file_absent_from_project(client, name: str, deadline: float | None = None) -> bool:
    """Fråvær er berre stadfesta etter ei fullført, avgrensa filliste."""
    try:
        files = client.files.list(config=types.ListFilesConfig(
            page_size=100, http_options=_http_options(_remaining_ms(deadline, 10_000))))
        for index, file in enumerate(files):
            if file.name == name or index >= 999 or (deadline is not None and time.monotonic() >= deadline):
                return False
        return True
    except Exception:
        return False


def _delete_files(client, names: list[str], on_deleted: Callable[[str], None] | None,
                  on_inaccessible: Callable[[str], None] | None = None,
                  *, deadline: float | None = None) -> list[str]:
    remaining = []
    for name in dict.fromkeys(names):
        if not FILE_NAME.fullmatch(name):
            remaining.append(name)
            continue
        removed = False
        for attempt in range(2):
            if deadline is not None and deadline - time.monotonic() < 0.001:
                break
            try:
                options = _http_options(_remaining_ms(deadline, 10_000))
                client.files.delete(name=name, config=types.DeleteFileConfig(http_options=options))
                removed = True
            except Exception as error:
                if _code(error) == 404:
                    removed = True
                elif (_code(error) == 403 and on_inaccessible is not None
                      and _file_absent_from_project(client, name, deadline)):
                    # Kan vere frå ein annan API-konto eller aldri ha blitt oppretta.
                    # Behald referansen: dette stadfestar IKKJE sletting.
                    on_inaccessible(name)
                elif (attempt == 0 and _transient(error)
                      and (deadline is None or time.monotonic() + 1 < deadline)):
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


def cleanup_files(names: list[str], on_deleted: Callable[[str], None] | None = None,
                  *, on_inaccessible: Callable[[str], None] | None = None,
                  deadline: float | None = None) -> list[str]:
    """Prøv berre sletting av tidlegare eigne filer, aldri opplasting eller analyse."""
    if not names or not configured():
        return list(names)
    client = None
    try:
        client = _client()
        return _delete_files(client, names, on_deleted, on_inaccessible, deadline=deadline)
    except Exception:
        return list(names)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def _wait_for_file(client, uploaded, job_deadline: float | None = None):
    deadline = time.monotonic() + FILE_READY_TIMEOUT_SECONDS
    if job_deadline is not None:
        deadline = min(deadline, job_deadline)
    current = uploaded
    for attempt in range(MAX_FILE_POLLS + 1):
        state = getattr(current.state, "name", current.state)
        if state == "ACTIVE":
            return current
        if state == "FAILED":
            raise GeminiError("Google kunne ikkje klargjere eit av videoklippa. Prøv eit anna klipp eller eit anna videoformat.", "file_failed")
        if state != "PROCESSING":
            raise GeminiError("Google returnerte ein ukjend filstatus. Analysen er stoppa.", "file_state")
        remaining_seconds = deadline - time.monotonic()
        if attempt == MAX_FILE_POLLS or remaining_seconds <= POLL_INTERVAL_SECONDS:
            raise GeminiError("Google brukte for lang tid på å klargjere videoen. Analysen er stoppa utan ny opplasting.", "file_timeout")
        time.sleep(POLL_INTERVAL_SECONDS)
        options = _http_options(_remaining_ms(deadline, 10_000))
        current = client.files.get(name=uploaded.name, config=types.GetFileConfig(http_options=options))
    raise GeminiError("Videoen vart ikkje klar innan tidsgrensa.", "file_timeout")


def ranking_requested(description: str) -> bool:
    """Gjenkjenn ei bestilling av rangering, med enkle eksplisitte avslag."""
    text = description.casefold()
    if re.search(r"\b(?:ikkje|ikke|utan|uten|without|no|do not|don't)\s+(?:ranger\w*|rank(?:ing)?|countdown)\b", text):
        return False
    return bool(re.search(r"\b(?:ranger\w*|rank(?:ing|ed)?|countdown)\b", text))


def _ranking_direction(description: str) -> str:
    """Bruk ordretning før talretning; 1 er alltid beste plassering."""
    text = description.casefold()
    best = r"(?:best(?:e)?|finast|finest)"
    worst = r"(?:verst(?:e)?|d[åa]rlegast|d[åa]rligst|worst)"
    separator = r"\s*(?:til|to|før|before|[-–—>→])+\s*"
    if re.search(best + separator + worst, text):
        return "best_first"
    if re.search(worst + separator + best, text):
        return "worst_first"
    numbers = re.search(r"\b(?:fr[åa]|from)\s*(\d{1,2})\s*(?:til|to|[-–—>→])\s*(\d{1,2})\b", text)
    if numbers:
        first, last = (int(value) for value in numbers.groups())
        if first != last and min(first, last) == 1:
            return "best_first" if first < last else "worst_first"
    return "best_first"


def _narration_requested(description: str) -> bool:
    text = description.casefold()
    speech = r"(?:snakk\w*|fortel\w*|fortell\w*|fortelj\w*|voice[ -]?over|narrat\w*|forteljarstemme|fortellerstemme|speak\w*)"
    filler = r"(?:\s+(?:add|include|use|ha|bruk|legg|leggje|legge|til|ta|med|ei|ein|en|noko|noen|any|a|an))*"
    if re.search(r"\b(?:ikkje|ikke|utan|uten|without|no|do not|don't)" + filler + r"\s+" + speech + r"\b", text):
        return False
    return bool(re.search(r"\b" + speech + r"\b", text))


def _requested_title(description: str, ranking: bool) -> str | None:
    # Berre tekst som brukaren knyter til ein synleg tittel blir tolka som fasit.
    quoted = re.search(
        r'(?:tittel\w*|overskrift\w*|tekst|title|text|står|seier|sier|says|reads)'
        r'[^.!?\n]{0,45}?["«“]([^"»”\n]+)["»”]',
        description, re.IGNORECASE,
    )
    if quoted:
        return quoted.group(1).strip()
    unquoted = re.search(
        r"(?:det\s+står|teksten\s+skal\s+vere|tittelen\s+skal\s+vere)\s+([^,.;!?\n]+)",
        description, re.IGNORECASE,
    )
    if unquoted:
        title = unquoted.group(1).strip()
        return "Rangering" if title.casefold() == "rangering" else title
    if ranking:
        return "Ranking" if re.search(r"\b(?:ranking|rank|ranked)\b", description, re.IGNORECASE) else "Rangering"
    return None


def editorial_requirements(description: str) -> dict:
    """Små, kontrollerbare krav; resten av bestillinga går uendra til modellen."""
    ranking = ranking_requested(description)
    title = _requested_title(description, ranking)
    if title is not None and (not title or len(title) > 60 or any(not char.isprintable() for char in title)):
        raise GeminiError("Opningstittelen må vere lesbar tekst på éi linje, høgst 60 teikn.", "instruction")
    return {"ranking_requested": ranking, "ranking_direction": _ranking_direction(description),
            "narration_requested": _narration_requested(description), "title": title}


def edit_instruction(description: str, duration_seconds: int, style: str, clip_count: int) -> dict:
    requirements = editorial_requirements(description)
    ranking = requirements["ranking_requested"]
    pacing = {
        "energetic": "Prioritize concise, visually strong moments and cut on action; avoid idle lead-ins.",
        "calm": "Give each moment room to breathe; preserve complete actions and natural endings.",
        "professional": "Use deliberate, varied shot lengths and clear continuity; remove dead time.",
    }[style]
    rules = [
        "You are the editor of these supplied videos. Watch all source clips before deciding the structure and excerpts.",
        "Follow the user's requested purpose, ordering, title and spoken content. Never substitute generic montage choices for explicit requirements.",
        "Choose meaningful moments based on visible content, not upload order or the first seconds by default.",
        "Do not spread time equally by default. Select strong starts and natural endpoints; preserve complete speech/actions where possible.",
        pacing,
        "Return ONLY the structured cut plan. No invented clips, URLs or file paths.",
        f"Use between 1 and {media.MAX_SEGMENTS} segments, each at least 1/24 second long.",
        "Use numeric seconds relative to each source. Times must be within its duration; do not repeat overlapping source ranges.",
        "Use unique order values 1..N. order is playback order, not a quality score.",
        "Sum of end_seconds minus start_seconds MUST equal target_duration_seconds within 0.002 seconds.",
        "Supported rendering: excerpt selection, ordering, fixed output aspect/fit, a short opening title, colorful upper-left ranking scoreboard, and local spoken voiceover when requested. Do not claim to add music, animated scenes, subtitles, sound effects or other unsupported effects.",
        "The opening title overlays the start of the first excerpt. Do not add extra time for a title card.",
        "Return presentation with title, ranking_direction and narration matching requirements. Use the exact required title if supplied. Otherwise set title to null unless the user requested an opening title.",
    ]
    if ranking:
        rank_order = "1, 2, ... N (best to worst)" if requirements["ranking_direction"] == "best_first" else "N, N-1, ... 1 (worst to best)"
        rules += [
            f"This is a ranking video of ALL {clip_count} source clips. Use exactly one representative excerpt per source clip.",
            "Compare clips using the user's stated criterion. If none is supplied, use subjective visual impact, composition and clarity; state that criterion explicitly.",
            f"Assign each clip a unique rank: rank 1 is best. Playback rank order MUST be {rank_order}; do not reverse the requested direction.",
            "Every segment MUST include ranking with rank, a short descriptive title (max 36 characters), a concrete reason (max 180 characters), and the identical criterion (max 60 characters).",
            "Reasons must cite visible differences between these actual clips and explain this placement. Do not invent facts or merely repeat the rank.",
            "Write title, criterion, reasons and voiceover in the user's language, each as one printable line.",
            "The renderer keeps a colorful upper-left ranking scoreboard, reveals each ranked entry as its clip begins, and highlights the current entry. Rank and title are displayed; reason and criterion are also shown beside the result.",
        ]
    else:
        rules += ["Set ranking to null. Build a purposeful opening, progression and ending matching the user's story, rather than an arbitrary collection of shots."]
    if requirements["narration_requested"]:
        rules += [
            "Every segment MUST include voiceover: a natural short spoken line about what is visible during that selected excerpt, not a technical editing instruction.",
            "Keep voiceover short enough to finish inside its own excerpt: at most floor(excerpt duration in seconds * 1.6) words, and no more than 220 characters. Select longer excerpts when speech needs room.",
            "Use concrete visible details and the tone the user asked for. If asked to praise the scenery, describe the observed light, colors, composition or mood warmly without inventing locations, history or unseen facts.",
            "For rankings, let the spoken line explain why the clip earns this position, rather than repeating the same generic compliment for every clip.",
        ]
    else:
        rules += ["Set every voiceover to null and presentation.narration to false; no speech was requested."]
    return {"instruction": description, "target_duration_seconds": duration_seconds,
            "style": style, "video_type": "ranking" if ranking else "montage",
            "requirements": requirements, "rules": rules}


def _generation_config(model: str) -> types.GenerateContentConfig:
    """Bruk ei eiga analysefrist; filopplasting og statusoppslag held sine grenser."""
    # 3.8 Flash har medium som standard. Low reduserer ventetid og tokenbruk,
    # medan andre modellar held standarden sin for å unngå ugyldige parametrar.
    known_flash = model.removeprefix("models/") == DEFAULT_MODEL
    thinking = types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW) if known_flash else None
    return types.GenerateContentConfig(
        response_mime_type="application/json",
        response_json_schema=CutPlan.model_json_schema(),
        max_output_tokens=4096,
        thinking_config=thinking,
        http_options=_http_options(ANALYSIS_TIMEOUT_MS),
        automatic_continuation=False,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )


def _generate_content_with_backoff(client, *, model, contents, config, progress, deadline=None, reserve_model=None):
    """Prøv berre avviste 503-analysar på nytt; aldri upload, kvote eller timeout."""
    for attempt in range(MAX_ANALYSIS_ATTEMPTS):
        active_model = reserve_model if attempt and reserve_model else model
        active_config = _generation_config(active_model) if active_model != model else config
        if attempt:
            progress(28, f"Analyserer med {active_model} (forsøk {attempt + 1} av {MAX_ANALYSIS_ATTEMPTS})")
        if deadline is not None:
            active_config = active_config.model_copy(update={
                "http_options": _http_options(_remaining_ms(deadline, ANALYSIS_TIMEOUT_MS)),
            })
        try:
            return client.models.generate_content(model=active_model, contents=contents, config=active_config)
        except Exception as error:
            # Ein transportfeil/timeout kan ha skjedd etter at Google byrja
            # arbeidet. Ikkje send ein mogleg betalt analyse ein gong til då.
            if _code(error) != 503:
                raise
            if attempt + 1 == MAX_ANALYSIS_ATTEMPTS:
                raise GeminiError(
                    f"Google-tenesta er framleis overbelasta eller mellombels utilgjengeleg "
                    f"under videoanalyse (HTTP 503), etter {MAX_ANALYSIS_ATTEMPTS} forsøk. "
                    "Klippa ligg framleis i prosjektet. Prøv seinare, eller vel enkel redigering utan AI. "
                    "Ingen fleire analyseforsøk blir starta automatisk.",
                    "unavailable", http_status=503, operation="analysis",
                ) from None
            delay = ANALYSIS_RETRY_DELAYS[attempt] + uniform(0, 1)
            if deadline is not None and time.monotonic() + delay >= deadline:
                raise TimeoutError() from None
            progress(28, f"Google er overbelasta (HTTP 503). Ventar {delay:.1f} sekund før "
                        f"forsøk {attempt + 2} av {MAX_ANALYSIS_ATTEMPTS}.")
            time.sleep(delay)


def _clip_metadata(clip: dict) -> types.Part:
    return types.Part.from_text(text=json.dumps({"clip_id": clip["id"], "duration_seconds": clip["duration"]}))


def _frame_video_parts(clips: list[dict], progress, job_deadline: float | None) -> list[types.Part]:
    """Klargjer minst eitt bilete per klipp før vi brukar tid på fleire bilete."""
    deadline = time.monotonic() + RENDER_FRAME_SECONDS
    if job_deadline is not None:
        deadline = min(deadline, job_deadline)
    by_clip = [[_clip_metadata(clip)] for clip in clips]
    bytes_left = RENDER_FRAME_MAX_BYTES
    fractions = (0.0, 0.45, 0.8)
    with TemporaryDirectory(prefix="klipp-bilete-") as temporary:
        destination = Path(temporary) / "frame.jpg"
        for pass_index, fraction in enumerate(fractions):
            for index, clip in enumerate(clips):
                seconds = round(clip["duration"] * fraction, 3)
                if fraction and clip["duration"] < 0.5:
                    continue
                completed = pass_index * len(clips) + index
                progress(3 + int(20 * completed / (len(fractions) * len(clips))),
                         f"Hentar bilete frå klipp {index + 1} av {len(clips)}")
                try:
                    frame_timeout = _remaining_ms(deadline, 8_000) / 1000
                    destination.unlink(missing_ok=True)
                    media.make_analysis_frame(clip, seconds, destination, frame_timeout)
                    size = destination.stat().st_size
                    if not 0 < size <= bytes_left:
                        raise media.MediaError("Bileta er for store til å sendast direkte.")
                    data = destination.read_bytes()
                except (media.MediaError, OSError, TimeoutError):
                    if fraction == 0:
                        raise GeminiError(
                            f"Appen kunne ikkje klargjere eit bilete frå klipp {index + 1} til Gemini.",
                            "frame_preparation",
                        ) from None
                    continue
                bytes_left -= size
                by_clip[index].extend([
                    types.Part.from_text(text=json.dumps({"sample_time_seconds": seconds})),
                    types.Part.from_bytes(data=data, mime_type="image/jpeg"),
                ])
    return [part for parts in by_clip for part in parts]


def _inline_video_parts(clips, fps, progress, job_deadline) -> list[types.Part]:
    deadline = time.monotonic() + PREPARATION_TIMEOUT_SECONDS
    if job_deadline is not None:
        deadline = min(deadline, job_deadline)
    parts = []
    bytes_left = INLINE_MAX_BYTES
    with TemporaryDirectory(prefix="klipp-analyse-") as temporary:
        for index, clip in enumerate(clips):
            progress(3 + int(20 * index / len(clips)), f"Lagar ei lita analysekopi av klipp {index + 1} av {len(clips)}")
            destination = Path(temporary) / f"clip-{index}.mp4"
            media.make_analysis_copy(clip, destination, _remaining_ms(deadline, PREPARATION_TIMEOUT_SECONDS * 1000) / 1000)
            size = destination.stat().st_size
            if not 0 < size <= bytes_left:
                raise media.MediaError("Analysekopiane er for store til å sendast direkte.")
            bytes_left -= size
            parts.extend([
                _clip_metadata(clip),
                types.Part(
                    inline_data=types.Blob(data=destination.read_bytes(), mime_type="video/mp4"),
                    video_metadata=types.VideoMetadata(fps=fps),
                ),
            ])
    return parts


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
    deadline: float | None = None,
    use_frames: bool = False,
) -> list[dict]:
    """Bruk tidsmerkte bilete på Render og video med lyd lokalt."""
    if not 1 <= len(clips) <= MAX_AI_FILES:
        raise GeminiError("AI-modus støttar mellom eitt og ti videoklipp per jobb.", "limits")
    source_seconds = sum(clip["duration"] for clip in clips)
    if source_seconds > MAX_AI_SOURCE_SECONDS:
        raise GeminiError("AI-modus støttar høgst 30 minutt samla kjeldemateriale per jobb.", "limits")
    if not description.strip():
        raise GeminiError("Skriv kva du ønskjer at Gemini skal lage før du startar AI-redigering.", "instruction")
    requirements = editorial_requirements(description)
    # Same førehandskontroll som enkel modus, utan å sende noko til Google.
    baseline_plan = media.build_plan(clips, duration_seconds, style)
    fps = min(1.0, ANALYSIS_FRAME_BUDGET / source_seconds)
    client = None
    remote_names = []
    failure = None
    plan = None
    operation = None
    try:
        client = _client()
        contents = []
        if use_frames:
            contents = _frame_video_parts(clips, progress, deadline)
            analysis_deadline = time.monotonic() + RENDER_ANALYSIS_SECONDS
            deadline = min(analysis_deadline, deadline) if deadline is not None else analysis_deadline
        elif source_seconds <= INLINE_SOURCE_SECONDS:
            try:
                contents = _inline_video_parts(clips, fps, progress, deadline)
            except (media.MediaError, OSError, TimeoutError):
                # Ingen analysekopi er send enno. File API kan framleis lese originalane.
                pass
        files_to_upload = [] if contents else clips
        for index, clip in enumerate(files_to_upload):
            progress(3 + int(20 * index / len(clips)), f"Sender klipp {index + 1} av {len(clips)} til Google")
            path = Path(clip["path"])
            mime = MIME_TYPES.get(path.suffix.lower())
            if not mime:
                raise GeminiError("Videoformatet kan ikkje sendast til Gemini. Bruk MP4, MOV, WebM eller MKV.", "file_type")
            operation = "upload"
            options = _http_options(_remaining_ms(deadline, 120_000))
            # Google støttar eit førehandsvalt filnamn. Journalfør FØR nettverkskallet:
            # då kan vi slette fila òg om upload-svaret aldri kjem fram.
            reserved_name = "files/" + uuid4().hex
            if on_uploaded:
                on_uploaded(reserved_name)
            remote_names.append(reserved_name)
            uploaded = client.files.upload(
                file=path,
                config=types.UploadFileConfig(name=reserved_name, mime_type=mime, http_options=options),
            )
            if uploaded.name != reserved_name:
                if isinstance(uploaded.name, str) and FILE_NAME.fullmatch(uploaded.name):
                    remote_names.append(uploaded.name)
                    if on_uploaded:
                        on_uploaded(uploaded.name)
                raise GeminiError("Google returnerte ein uventa filreferanse. Analysen er stoppa.", "file_reference")
            progress(4 + int(20 * index / len(clips)), f"Google klargjer klipp {index + 1} av {len(clips)}")
            operation = "file_processing"
            ready = _wait_for_file(client, uploaded, deadline)
            if not ready.uri or not ready.mime_type:
                raise GeminiError("Google returnerte ufullstendige videometadata.", "file_reference")
            # ID og videoreferanse blir para tydeleg. Lokale stiar/filnamn blir ikkje sende.
            contents.extend([
                _clip_metadata(clip),
                types.Part(
                    file_data=types.FileData(file_uri=ready.uri, mime_type=ready.mime_type),
                    video_metadata=types.VideoMetadata(fps=fps),
                ),
            ])
        instruction = edit_instruction(description, duration_seconds, style, len(clips))
        if use_frames:
            instruction["rules"][0] = (
                "Analyze the timestamped images from each clip and use the visible content and the user's "
                "instruction to choose excerpts and playback order. Treat the images as samples. "
                "Audio is not provided, so do not infer speech or sounds."
            )
            instruction["rules"][3] = (
                "Select visually strong excerpts. You may use the suggested lengths, which already sum to "
                "the target duration, and choose each start time and playback order yourself."
            )
            instruction["suggested_lengths"] = [
                {"clip_id": segment["clip_id"], "duration_seconds": round(segment["duration"], 6)}
                for segment in baseline_plan
            ]
        contents.append(types.Part.from_text(text=json.dumps(instruction, ensure_ascii=False)))
        progress(28, "Gemini analyserer klippa og lagar ein klippeplan")
        operation = "analysis"
        selected_model = model or model_name()
        reserve_model = (RENDER_RESERVE_MODEL
                         if use_frames and selected_model.removeprefix("models/") == DEFAULT_MODEL else None)
        response = _generate_content_with_backoff(
            client, progress=progress,
            model=selected_model, contents=contents,
            config=_generation_config(selected_model), deadline=deadline, reserve_model=reserve_model,
        )
        progress(40, "Validerer klippeplanen frå Gemini")
        try:
            plan = parse_plan(
                response.text, clips, duration_seconds,
                require_ranking=requirements["ranking_requested"],
                ranking_direction=requirements["ranking_direction"] if requirements["ranking_requested"] else None,
                narration_requested=requirements["narration_requested"], expected_title=requirements["title"],
            )
        except media.MediaError as error:
            # parse_plan gir berre eigne, sanerte meldingar, aldri rå AI-svar.
            raise GeminiError(f"Gemini-planen kunne ikkje brukast: {error} Ingen video er eksportert.", "invalid_plan") from None
        if remote_names:
            progress(42, "Ryddar mellombelse videofiler hos Google")
    except GeminiError as error:
        failure = error
    except Exception as error:
        failure = _safe_error(error, operation=operation)
    finally:
        cleanup_deadline = time.monotonic() + CLEANUP_WAIT_SECONDS if deadline is not None else None
        remaining = (_delete_files(client, remote_names, on_deleted, deadline=cleanup_deadline)
                     if client is not None else remote_names)
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
    if remaining:
        message = "Mellombelse Google-filer kunne ikkje slettast. Filreferansane er lagra for ny opprydding ved neste oppstart eller jobb. Ingen ny analyse blir starta av oppryddinga."
        if failure:
            message = str(failure) + " " + message
        raise GeminiError(message, "cleanup",
                          http_status=failure.http_status if failure else None,
                          operation=failure.operation if failure else None) from None
    if failure:
        raise failure from None
    return plan
