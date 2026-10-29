"""Avgrensa, lokal videobehandling med FFmpeg.

Ein klippeplan er data, aldri ein shell-kommando. Enkel modus tek ein
proporsjonal del frå starten av kvart klipp, i opplastingsrekkefølgje.
Ingen av funksjonane her gjer AI-analyse eller tolkar tekstinstruksjonar.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Callable


MAX_SEGMENTS = 12
MAX_EXPORT_SECONDS = 600
ASPECTS = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (720, 720)}
FADES = {"calm": 0.6, "energetic": 0.0, "professional": 0.25}


class MediaError(Exception):
    """Ein lesbar feil som kan visast i det lokale grensesnittet."""


def _binary(name: str) -> str:
    configured = os.environ.get(f"{name.upper()}_BIN", name)
    executable = shutil.which(configured)
    if not executable:
        raise MediaError(
            f"{name} vart ikkje funne. Installer FFmpeg, og legg både ffmpeg og "
            "ffprobe i PATH, eller konfigurer FFMPEG_BIN og FFPROBE_BIN."
        )
    return executable


def tools_available() -> bool:
    """Berre ei tilgjengesjekk; faktisk lesing/rendering må framleis testast."""
    try:
        _binary("ffmpeg")
        _binary("ffprobe")
        return True
    except MediaError:
        return False


def _number(value: object, label: str) -> float:
    try:
        if isinstance(value, bool):
            raise ValueError
        result = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise MediaError(f"{label} må vere eit gyldig tal.") from exc
    if not math.isfinite(result):
        raise MediaError(f"{label} må vere eit endeleg tal.")
    return result


def _local_file(path: Path | str) -> Path:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise MediaError("Videofila finst ikkje, eller er ikkje ei vanleg lokal fil.")
    return path.resolve()


def _run(command: list[str], timeout: float, failure: str) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaError("Videobehandlinga tok for lang tid og vart stoppa.") from exc
    except OSError as exc:
        raise MediaError("Kunne ikkje starte FFmpeg/ffprobe. Kontroller installasjonen.") from exc
    if result.returncode:
        # FFmpeg-utdata kan innehalde lokale filstiar. Ikkje send dei til nettlesaren.
        raise MediaError(failure)
    return result


def probe(path: Path) -> dict:
    """Les og valider videometadata; nettverksprotokollar/playlistar er avviste."""
    path = _local_file(path)
    result = _run(
        [
            _binary("ffprobe"), "-v", "error",
            "-protocol_whitelist", "file,pipe",
            "-format_whitelist", "mov,matroska,webm",
            "-show_entries",
            "stream=codec_type,codec_name,width,height,duration:"
            "stream_disposition=attached_pic:format=format_name,duration",
            "-of", "json", str(path),
        ],
        timeout=30,
        failure="Fila kan ikkje lesast som ein støtta video (MP4, MOV, MKV eller WebM).",
    )
    try:
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        videos = [stream for stream in streams if stream.get("codec_type") == "video"]
        if not videos or videos[0].get("disposition", {}).get("attached_pic"):
            raise MediaError("Fila må innehalde ein ekte videostraumen, ikkje berre lyd eller eit omslagsbilete.")
        video = videos[0]
        width, height = int(video.get("width", 0)), int(video.get("height", 0))
        if (
            not video.get("codec_name")
            or min(width, height) < 1
            or max(width, height) > 7680
            or width * height > 7680 * 4320
        ):
            raise MediaError("Videoen har ugyldig eller for høg oppløysing. Maksimum er 8K.")
        format_data = data.get("format", {})
        format_name = format_data.get("format_name", "")
        if not set(format_name.split(",")) & {"mov", "mp4", "matroska", "webm"}:
            raise MediaError("Videobehaldaren er ikkje støtta. Bruk MP4, MOV, MKV eller WebM.")
        # Videolengda er betre enn containerlengda når lydsporet er lengre.
        duration_value = video.get("duration")
        if duration_value in (None, "N/A"):
            duration_value = format_data.get("duration")
        duration = _number(duration_value, "Videolengda")
        if duration <= 0:
            raise MediaError("Videoen må ha ei positiv, kjend lengd.")
        return {
            "duration": duration,
            "width": width,
            "height": height,
            "has_audio": any(stream.get("codec_type") == "audio" for stream in streams),
            "format_name": format_name,
        }
    except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
        raise MediaError("Kunne ikkje lese gyldige videometadata frå fila.") from exc


def build_plan(clips: list[dict], duration_seconds: float, style: str) -> list[dict]:
    """Vel proporsjonale utdrag, utan tilfeldige kutt eller påstått AI-analyse."""
    target = _number(duration_seconds, "Ønskt lengd")
    if style not in FADES:
        raise MediaError("Vel ein gyldig redigeringsstil.")
    if not clips or len(clips) > MAX_SEGMENTS:
        raise MediaError(f"Last opp mellom eitt og {MAX_SEGMENTS} videoklipp.")
    durations = [_number(clip.get("duration"), "Klipplengda") for clip in clips]
    if any(duration <= 0 for duration in durations):
        raise MediaError("Alle kjeldeklipp må ha ei positiv lengd.")
    total = sum(durations)
    if target <= 0 or target > MAX_EXPORT_SECONDS:
        raise MediaError("Eksporten må vere lengre enn null og høgst 10 minutt.")
    if target > total + 0.001:
        raise MediaError(
            f"Kjeldematerialet er berre {total:.1f} sekund langt. "
            "Vel ei kortare lengd eller last opp fleire klipp."
        )
    # Ein toleranse på eitt millisekund dekkjer avrunda metadata, utan å strekke video.
    target = min(target, total)
    plan = [
        {
            "clip_id": clip["id"],
            "path": str(_local_file(clip["path"])),
            "start": 0.0,
            "duration": target * duration / total,
            "has_audio": bool(clip.get("has_audio")),
        }
        for clip, duration in zip(clips, durations)
    ]
    validate_plan(plan, clips, duration_seconds)
    return plan


def validate_plan(plan: list[dict], clips: list[dict], target_duration: float) -> None:
    """Sjekk også planar som seinare kan kome frå ei AI-teneste, før FFmpeg."""
    target = _number(target_duration, "Ønskt lengd")
    if target <= 0 or target > MAX_EXPORT_SECONDS:
        raise MediaError("Eksporten må vere lengre enn null og høgst 10 minutt.")
    if not isinstance(plan, list) or not 1 <= len(plan) <= MAX_SEGMENTS:
        raise MediaError("Klippeplanen har eit ugyldig tal utdrag.")
    sources = {clip["id"]: clip for clip in clips}
    if len(sources) != len(clips):
        raise MediaError("Klippeplanen har dupliserte kjelde-ID-ar.")
    total = 0.0
    for segment in plan:
        if not isinstance(segment, dict) or segment.get("clip_id") not in sources:
            raise MediaError("Klippeplanen viser til eit ukjent klipp.")
        source = sources[segment["clip_id"]]
        if _local_file(segment.get("path", "")) != _local_file(source["path"]):
            raise MediaError("Klippeplanen viser til feil kjeldefil.")
        start = _number(segment.get("start"), "Startpunktet")
        duration = _number(segment.get("duration"), "Utdragslengda")
        source_duration = _number(source.get("duration"), "Kjelden si lengd")
        if start < 0 or duration <= 0 or start + duration > source_duration + 0.001:
            raise MediaError("Eit utdrag går utanfor lengda til kjeldeklippet.")
        if not isinstance(segment.get("has_audio"), bool) or segment["has_audio"] != bool(source.get("has_audio")):
            raise MediaError("Lydinformasjonen i klippeplanen stemmer ikkje med kjelda.")
        total += duration
    if abs(total - target) > 0.002:
        raise MediaError("Lengda til klippeplanen stemmer ikkje med ønskt eksportlengd.")


def render(
    plan: list[dict],
    output_path: Path,
    aspect: str,
    fit: str,
    style: str,
    progress: Callable[[int, str], None],
) -> dict:
    """Normaliser utdrag, set dei saman, og kontroller den ferdige MP4-fila."""
    if aspect not in ASPECTS or fit not in {"pad", "crop"} or style not in FADES:
        raise MediaError("Ugyldig format, biletetilpassing eller redigeringsstil.")
    if not isinstance(plan, list) or not 1 <= len(plan) <= MAX_SEGMENTS:
        raise MediaError("Klippeplanen må innehalde mellom eitt og tolv utdrag.")
    duration_sum = 0.0
    for segment in plan:
        _local_file(segment["path"])
        duration = _number(segment.get("duration"), "Utdragslengda")
        start = _number(segment.get("start"), "Startpunktet")
        if duration <= 0 or start < 0:
            raise MediaError("Ugyldige klippepunkt i planen.")
        duration_sum += duration
    if duration_sum > MAX_EXPORT_SECONDS + 0.002:
        raise MediaError("Klippeplanen er lengre enn 10 minutt.")

    ffmpeg = _binary("ffmpeg")
    width, height = ASPECTS[aspect]
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        progress(5, "Klippeplanen er kontrollert")
        with tempfile.TemporaryDirectory(prefix="render-", dir=output_path.parent) as temporary:
            workdir = Path(temporary)
            for index, segment in enumerate(plan):
                duration = float(segment["duration"])
                start = float(segment["start"])
                destination = workdir / f"segment-{index:02d}.mp4"
                video_filter = (
                    f"scale={width}:{height}:force_original_aspect_ratio="
                    f"{'decrease' if fit == 'pad' else 'increase'}:force_divisible_by=2,"
                )
                if fit == "pad":
                    video_filter += f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
                else:
                    video_filter += f"crop={width}:{height},"
                video_filter += "setsar=1,setpts=PTS-STARTPTS,fps=24"
                audio_filter = (
                    "aresample=48000,aformat=channel_layouts=stereo,"
                    f"asetpts=PTS-STARTPTS,apad,atrim=duration={duration:.6f}"
                )
                fade = min(FADES[style], duration / 3) if duration >= 0.15 else 0
                if fade:
                    end = duration - fade
                    video_filter += f",fade=t=in:st=0:d={fade:.6f},fade=t=out:st={end:.6f}:d={fade:.6f}"
                    audio_filter += f",afade=t=in:st=0:d={fade:.6f},afade=t=out:st={end:.6f}:d={fade:.6f}"

                command = [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                    "-threads", "2", "-filter_threads", "1",
                    "-protocol_whitelist", "file,pipe",
                    "-format_whitelist", "mov,matroska,webm",
                    "-ss", f"{start:.6f}", "-i", str(_local_file(segment["path"])),
                ]
                if not segment.get("has_audio"):
                    command += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
                command += [
                    "-map", "0:v:0", "-map", "0:a:0" if segment.get("has_audio") else "1:a:0",
                    "-vf", video_filter, "-af", audio_filter,
                    "-t", f"{duration:.6f}",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                    "-maxrate", "3M", "-bufsize", "6M",
                    "-pix_fmt", "yuv420p", "-threads", "2",
                    "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
                    "-map_metadata", "-1", "-sn", "-dn",
                    "-movflags", "+faststart", str(destination),
                ]
                _run(
                    command,
                    timeout=min(900, max(60, duration * 4 + 30)),
                    failure=f"Kunne ikkje behandle klipp {index + 1}. Prøv ei anna videofil.",
                )
                progress(10 + int(80 * (index + 1) / len(plan)), f"Klipp {index + 1} av {len(plan)} er ferdig")

            # Berre eigne, genererte filnamn går inn i concat-lista.
            manifest = workdir / "segments.txt"
            manifest.write_text(
                "".join(f"file 'segment-{index:02d}.mp4'\n" for index in range(len(plan))),
                encoding="utf-8",
            )
            merged = workdir / "result.mp4"
            _run(
                [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                    "-protocol_whitelist", "file,pipe", "-f", "concat", "-safe", "1",
                    "-i", str(manifest), "-map", "0:v:0", "-map", "0:a:0",
                    "-c", "copy", "-map_metadata", "-1", "-movflags", "+faststart", str(merged),
                ],
                timeout=120,
                failure="Kunne ikkje setje saman klippa til ein ferdig video.",
            )
            progress(95, "Videoen er sett saman; kontrollerer eksporten")
            result = probe(merged)
            if (result["width"], result["height"]) != (width, height) or not result["has_audio"]:
                raise MediaError("Eksporten fekk eit uventa format og vart ikkje lagra.")
            # FPS- og AAC-avrunding kan gje nokre ekstra hundredelar per utdrag.
            if abs(result["duration"] - duration_sum) > max(0.5, len(plan) / 24 + 0.1):
                raise MediaError("Eksportlengda avvik for mykje frå klippeplanen.")
            merged.replace(output_path)
            progress(100, "Videoen er ferdig")
            return result
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
