"""Lokal Windows-forteljarstemme utan API, opplasting eller registerendringar.

OneCore-stemmer (mellom anna norske Microsoft Jon) blir lesne via WinRT.
Brukar-/modelltekst blir overført som UTF-8-data, aldri som PowerShell-kode.
"""

from __future__ import annotations

from functools import lru_cache
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import wave

from .media import MediaError


MAX_TEXT_CHARACTERS = 2000
SPEECH_TIMEOUT_SECONDS = 45
_REQUEST_ENV = "KLIPP_LOCAL_SPEECH_REQUEST"
_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$synth = $null
$stream = $null
$reader = $null
$writer = $null
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    [Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media.SpeechSynthesis, ContentType=WindowsRuntime] | Out-Null
    [Windows.Media.SpeechSynthesis.SpeechSynthesisStream, Windows.Media.SpeechSynthesis, ContentType=WindowsRuntime] | Out-Null
    $request = Get-Content -LiteralPath $env:KLIPP_LOCAL_SPEECH_REQUEST -Raw -Encoding UTF8 | ConvertFrom-Json
    $voice = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices |
        Where-Object { $_.Language -eq $request.language } | Select-Object -First 1
    if ($null -eq $voice) {
        @{available=$false; language=$request.language; voice=$null} | ConvertTo-Json -Compress
        exit 0
    }
    if ($request.operation -eq 'synthesize') {
        $synth = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
        $synth.Voice = $voice
        $operation = $synth.SynthesizeTextToStreamAsync([string]$request.text)
        $asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() |
            Where-Object {
                $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and
                $_.GetGenericArguments().Count -eq 1 -and
                $_.GetParameters().Count -eq 1 -and
                $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
            } | Select-Object -First 1
        $task = $asTask.MakeGenericMethod([Windows.Media.SpeechSynthesis.SpeechSynthesisStream]).Invoke($null, @($operation))
        $stream = $task.GetAwaiter().GetResult()
        $reader = [System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($stream)
        $writer = [System.IO.File]::Create([string]$request.output)
        $reader.CopyTo($writer)
    }
    @{available=$true; language=$voice.Language; voice=$voice.DisplayName} | ConvertTo-Json -Compress
} catch {
    [Console]::Error.WriteLine('LOCAL_SPEECH_ERROR')
    exit 1
} finally {
    if ($null -ne $writer) { $writer.Dispose() }
    if ($null -ne $reader) { $reader.Dispose() }
    if ($null -ne $stream) { $stream.Dispose() }
    if ($null -ne $synth) { $synth.Dispose() }
}
"""


def _language(language: str) -> str:
    if not isinstance(language, str):
        raise MediaError("Vel eit gyldig språk for forteljarstemma.")
    if language.lower() in {"no", "nb", "nn", "nb-no", "nn-no", "no-no"}:
        return "nb-NO"
    # WinRT vel berre ei stemme med nøyaktig samsvarande språk.
    if len(language) == 5 and language[2] == "-" and language.replace("-", "").isascii() and language.replace("-", "").isalpha():
        return language[:2].lower() + "-" + language[3:].upper()
    raise MediaError("Vel eit gyldig språk for forteljarstemma.")


def _invoke(request: dict, *, timeout: int) -> dict:
    if sys.platform != "win32":
        return {"available": False, "language": request["language"], "voice": None}
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    # Fila og prosessen er kortlevde; ingen forteljartekst blir lagra i loggar.
    with tempfile.TemporaryDirectory(prefix="klipp-speech-") as temporary:
        request_path = Path(temporary) / "request.json"
        request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        environment = {key: value for key, value in os.environ.items()
                       if key not in {"GEMINI_API_KEY", "GOOGLE_API_KEY"}}
        environment[_REQUEST_ENV] = str(request_path)
        try:
            result = subprocess.run(
                [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                 base64.b64encode(_SCRIPT.encode("utf-16-le")).decode("ascii")],
                env=environment, stdin=subprocess.DEVNULL, capture_output=True,
                timeout=timeout, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            raise MediaError("Den lokale forteljarstemma brukte for lang tid. Prøv ein kortare tekst.") from None
        except OSError:
            raise MediaError("Kunne ikkje starte den lokale Windows-forteljarstemma.") from None
        if result.returncode:
            raise MediaError("Den lokale Windows-forteljarstemma kunne ikkje lage lyd.")
        try:
            # PowerShell Console.OutputEncoding kan variere; dei aktuelle felta
            # er ASCII for Windows-stemmene vi brukar, men dekod feil varsamt.
            info = json.loads(result.stdout.decode("utf-8-sig", errors="replace").strip())
            if not isinstance(info, dict) or not isinstance(info.get("available"), bool):
                raise ValueError
            if info["available"] and (info.get("language") != request["language"] or not isinstance(info.get("voice"), str)):
                raise ValueError
            return {"available": info["available"], "language": request["language"],
                    "voice": info.get("voice") if info["available"] else None}
        except (ValueError, TypeError):
            raise MediaError("Fekk ikkje eit gyldig svar frå den lokale forteljarstemma.") from None


@lru_cache(maxsize=8)
def _voice(language: str) -> tuple[bool, str | None]:
    try:
        info = _invoke({"operation": "availability", "language": language}, timeout=10)
        return info["available"], info["voice"]
    except MediaError:
        return False, None


def availability(language: str = "nb-NO") -> dict:
    """Returner berre stemmestatus; ikkje innhald, filstiar eller nøkkelverdiar."""
    language = _language(language)
    available, voice = _voice(language)
    return {"available": available, "language": language, "voice": voice}


def synthesize(text: str, output_path: Path | str, language: str = "nb-NO") -> dict:
    """Lag ei WAV-fil med ei lokal stemme på det valde språket.

    Lange manus og kontrollteikn blir avviste før prosessen blir starta.
    Funksjonen overskriv ikkje ei eksisterande fil.
    """
    language = _language(language)
    if (not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARACTERS
            or any(not character.isprintable() and character not in "\n\r\t" for character in text)):
        raise MediaError("Forteljarmanuset må vere lesbar tekst på mellom 1 og 2000 teikn.")
    output = Path(output_path).absolute()
    if output.suffix.lower() != ".wav" or output.exists() or output.is_symlink():
        raise MediaError("Forteljarstemma må lagrast som ei ny, lokal WAV-fil.")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        info = _invoke({"operation": "synthesize", "language": language,
                        "text": text.strip(), "output": str(output)}, timeout=SPEECH_TIMEOUT_SECONDS)
        if not info["available"]:
            raise MediaError("Ei lokal forteljarstemme på det valde språket er ikkje installert. Installer stemma i Windows, eller slå av forteljarstemme.")
        with wave.open(str(output), "rb") as sound:
            rate, frames = sound.getframerate(), sound.getnframes()
            if rate <= 0 or frames <= 0 or sound.getnchannels() not in (1, 2):
                raise ValueError
            duration = frames / rate
        return {"path": str(output), "duration": duration,
                "voice": info["voice"], "language": language}
    except MediaError:
        output.unlink(missing_ok=True)
        raise
    except (OSError, EOFError, ValueError, wave.Error):
        output.unlink(missing_ok=True)
        raise MediaError("Den lokale forteljarstemma laga ikkje ei gyldig lydfil.") from None
