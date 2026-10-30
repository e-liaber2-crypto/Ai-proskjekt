"""Les ein AI-klippeplan som utrygge data, og bind han til lokale kjelder.

Modellen får berre velje klipp-ID, tidspunkt og rekkjefølgje. Filstiar og
lydinformasjon kjem alltid frå serveren. Ingen AI-utdata blir køyrde som kode.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import media


MAX_RESPONSE_CHARACTERS = 64 * 1024
MIN_SEGMENT_SECONDS = 1 / 24


class Segment(BaseModel):
    """Den avgrensa informasjonen AI-tenesta får bestemme per utdrag."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    clip_id: str = Field(strict=True, min_length=1)
    start_seconds: float = Field(strict=True, ge=0, allow_inf_nan=False)
    end_seconds: float = Field(strict=True, gt=0, allow_inf_nan=False)
    order: int = Field(strict=True, ge=1, le=media.MAX_SEGMENTS)


class CutPlan(BaseModel):
    """Skjemaet blir også sendt til Gemini for eit strukturert JSON-svar."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    segments: list[Segment] = Field(min_length=1, max_length=media.MAX_SEGMENTS)


def _reject_constant(_value: str):
    # JSON tillèt ikkje NaN/Infinity, sjølv om Python sin parser elles gjer det.
    raise ValueError("Ugyldig JSON-konstant")


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplisert JSON-nøkkel")
        result[key] = value
    return result


def _source_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise media.MediaError("Kjeldeklippa manglar gyldig lengdeinformasjon.")
    try:
        number = float(value)
    except (ValueError, OverflowError):
        raise media.MediaError("Kjeldeklippa manglar gyldig lengdeinformasjon.") from None
    if not math.isfinite(number) or number <= 0:
        raise media.MediaError("Kjeldeklippa må ha ei positiv, kjend lengd.")
    return number


def parse_plan(text: str, clips: list[dict], target_duration: float) -> list[dict]:
    """Avvis ugyldige svar, og returner ein plan som FFmpeg-motoren kan bruke.

    Feiltekstane inneheld aldri modellsvaret eller Pydantic sine rå feil, som
    kan innehalde sensitivt innhald. Unntakskjeder blir skjulte av same grunn.
    """
    if not isinstance(text, str) or len(text) > MAX_RESPONSE_CHARACTERS:
        raise media.MediaError("AI-svaret manglar eller er for stort til å vere ein gyldig klippeplan.")
    try:
        decoded = json.loads(
            text,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except (ValueError, TypeError, RecursionError):
        raise media.MediaError(
            "AI-svaret er ikkje gyldig JSON utan dupliserte nøklar. "
            "Prøv på nytt, eller bruk enkel redigering."
        ) from None
    try:
        cut_plan = CutPlan.model_validate(decoded)
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise media.MediaError(
            "AI-klippeplanen har ugyldige felt. Han må innehalde eitt til tolv "
            "utdrag med klipp-ID, numeriske tidspunkt og heiltalsrekkjefølgje."
        ) from None

    if isinstance(target_duration, bool) or not isinstance(target_duration, (int, float)):
        raise media.MediaError("Ønskt eksportlengd må vere eit gyldig tal.")
    try:
        target = float(target_duration)
    except (ValueError, OverflowError):
        raise media.MediaError("Ønskt eksportlengd må vere eit gyldig tal.") from None
    if not math.isfinite(target) or not 0 < target <= media.MAX_EXPORT_SECONDS:
        raise media.MediaError("Eksporten må vere lengre enn null og høgst ti minutt.")

    if not isinstance(clips, list) or not 1 <= len(clips) <= media.MAX_SEGMENTS:
        raise media.MediaError("Vel mellom eitt og tolv gyldige kjeldeklipp.")
    sources = {}
    for clip in clips:
        if not isinstance(clip, dict) or not isinstance(clip.get("id"), str) or not clip["id"]:
            raise media.MediaError("Eit kjeldeklipp manglar ein gyldig ID.")
        if clip["id"] in sources:
            raise media.MediaError("Kjeldeklippa har dupliserte ID-ar.")
        duration = _source_number(clip.get("duration"))
        try:
            original_path = Path(clip["path"])
            if original_path.is_symlink() or not original_path.is_file():
                raise OSError
            local_path = original_path.resolve(strict=True)
        except (KeyError, TypeError, ValueError, OSError, RuntimeError):
            raise media.MediaError(
                "Ei lokal kjeldefil manglar eller er ikkje ei vanleg videofil. Last opp klippet på nytt."
            ) from None
        sources[clip["id"]] = {
            "path": str(local_path),
            "duration": duration,
            "has_audio": bool(clip.get("has_audio")),
        }

    ordered = sorted(cut_plan.segments, key=lambda segment: segment.order)
    if [segment.order for segment in ordered] != list(range(1, len(ordered) + 1)):
        raise media.MediaError("Utdraga må ha unik rekkjefølgje frå 1 til talet på utdrag, utan hol.")

    plan = []
    source_intervals: dict[str, list[tuple[float, float]]] = {}
    for segment in ordered:
        source = sources.get(segment.clip_id)
        if source is None:
            raise media.MediaError("AI-klippeplanen viser til eit klipp som ikkje høyrer til prosjektet.")
        start, end = segment.start_seconds, segment.end_seconds
        duration = end - start
        if end <= start:
            raise media.MediaError("Sluttpunktet til kvart utdrag må vere etter startpunktet.")
        if start >= source["duration"] or end > source["duration"]:
            raise media.MediaError("Eit AI-utdrag går utanfor lengda til kjeldeklippet.")
        if duration + 1e-9 < MIN_SEGMENT_SECONDS:
            raise media.MediaError("Kvart AI-utdrag må vare minst éi videoramme (1/24 sekund).")
        source_intervals.setdefault(segment.clip_id, []).append((start, end))
        plan.append(
            {
                "clip_id": segment.clip_id,
                "path": source["path"],
                "start": start,
                "duration": duration,
                "has_audio": source["has_audio"],
            }
        )

    for intervals in source_intervals.values():
        chronological = sorted(intervals)
        if any(current[0] < previous[1] for previous, current in zip(chronological, chronological[1:])):
            raise media.MediaError("AI-klippeplanen brukar overlappande utdrag frå det same kjeldeklippet.")

    if abs(math.fsum(segment["duration"] for segment in plan) - target) > 0.002:
        raise media.MediaError("Lengda til AI-klippeplanen stemmer ikkje med ønskt eksportlengd.")
    # Motoren gjer ein siste, uavhengig kontroll av kjelde-ID, filsti, lyd og tid.
    media.validate_plan(plan, clips, target)
    return plan
