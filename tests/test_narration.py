"""Lokal tale: trygg dataoverføring, feilhandsaming og ekte Windows-WAV."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch
import wave

from app import narration
from app.media import MediaError


class NarrationTests(unittest.TestCase):
    def setUp(self):
        narration._voice.cache_clear()
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(narration._voice.cache_clear)
        self.directory = Path(self.temporary.name)

    def test_voice_probe_is_cached_and_nynorsk_uses_norwegian_not_english(self):
        info = {"available": True, "voice": "Microsoft Jon", "language": "nb-NO"}
        with patch.object(narration, "_invoke", return_value=info) as invoke:
            first = narration.availability("nn-NO")
            first["voice"] = "changed by caller"
            second = narration.availability("nb-NO")
        self.assertEqual(second, info)
        self.assertEqual(invoke.call_count, 1)

    def test_text_and_paths_are_json_data_never_powershell_source(self):
        text = "Ein tekst med 'sitat', $() og `teikn`.\nIkkje ein kommando."
        request = {"operation": "synthesize", "language": "nb-NO", "text": text,
                   "output": str(self.directory / "name with ' quote.wav")}

        def run(command, **kwargs):
            self.assertNotIn(text, " ".join(command))
            self.assertNotIn(request["output"], " ".join(command))
            self.assertNotIn("GEMINI_API_KEY", kwargs["env"])
            self.assertNotIn("GOOGLE_API_KEY", kwargs["env"])
            request_path = Path(kwargs["env"][narration._REQUEST_ENV])
            self.assertEqual(json.loads(request_path.read_text(encoding="utf-8")), request)
            self.assertEqual(kwargs["timeout"], 5)
            return SimpleNamespace(returncode=0, stdout=b'{"available":true,"language":"nb-NO","voice":"Jon"}')

        with patch.object(narration.sys, "platform", "win32"), patch.dict(os.environ, {
            "GEMINI_API_KEY": "private-test-value", "GOOGLE_API_KEY": "private-test-value",
        }), patch.object(narration.subprocess, "run", side_effect=run):
            self.assertTrue(narration._invoke(request, timeout=5)["available"])

    def test_invalid_text_does_not_launch_process(self):
        for value in ("", " \n", "a" * 2001, "bad\x00text", None):
            with self.subTest(value_kind=type(value).__name__), patch.object(narration, "_invoke") as invoke:
                with self.assertRaises(MediaError):
                    narration.synthesize(value, self.directory / "voice.wav")
                invoke.assert_not_called()

    def test_existing_output_is_not_overwritten_or_removed(self):
        output = self.directory / "voice.wav"
        output.write_bytes(b"preserve this file")
        with patch.object(narration, "_invoke") as invoke, self.assertRaises(MediaError):
            narration.synthesize("Ein liten test.", output)
        invoke.assert_not_called()
        self.assertEqual(output.read_bytes(), b"preserve this file")

    def test_unavailable_language_does_not_fall_back_to_foreign_voice(self):
        with patch.object(narration, "_invoke", return_value={"available": False, "language": "nb-NO", "voice": None}):
            with self.assertRaisesRegex(MediaError, "ikkje installert"):
                narration.synthesize("Ein liten test.", self.directory / "voice.wav")
        self.assertFalse((self.directory / "voice.wav").exists())

    def test_process_timeout_is_sanitized(self):
        private = "private text and path"
        with patch.object(narration.sys, "platform", "win32"), patch.object(
            narration.subprocess, "run", side_effect=subprocess.TimeoutExpired(private, 1, output=private),
        ):
            with self.assertRaises(MediaError) as raised:
                narration._invoke({"operation": "availability", "language": "nb-NO"}, timeout=1)
        self.assertNotIn(private, str(raised.exception))

    def test_invalid_generated_audio_is_removed(self):
        output = self.directory / "voice.wav"

        def invalid_audio(*args, **kwargs):
            output.write_bytes(b"not a wave file")
            return {"available": True, "language": "nb-NO", "voice": "Jon"}

        with patch.object(narration, "_invoke", side_effect=invalid_audio):
            with self.assertRaises(MediaError):
                narration.synthesize("Ein liten test.", output)
        self.assertFalse(output.exists())

    @unittest.skipUnless(sys.platform == "win32", "Krev ei lokal Windows-stemme")
    def test_installed_norwegian_voice_generates_audio_without_network(self):
        if not narration.availability()["available"]:
            self.skipTest("Norsk Windows-stemme er ikkje installert")
        output = self.directory / "voice.wav"
        result = narration.synthesize("Dette er ein lokal test av forteljarstemma.", output)
        self.assertEqual(result["language"], "nb-NO")
        self.assertGreater(result["duration"], 0.2)
        self.assertLess(result["duration"], 20)
        with wave.open(str(output), "rb") as sound:
            samples = sound.readframes(sound.getnframes())
        self.assertTrue(any(samples))


if __name__ == "__main__":
    unittest.main()
