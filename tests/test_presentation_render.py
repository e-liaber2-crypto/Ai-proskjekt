"""Verkeleg lokal eksport av rangering og tale, utan Google-kall."""

from pathlib import Path
from tempfile import TemporaryDirectory
import shutil
import subprocess
import unittest
from unittest.mock import patch

from app import media, narration, presentation


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg trengst")
class PresentationRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.source = cls.root / "source.mp4"
        subprocess.run([
            media._binary("ffmpeg"), "-v", "error", "-nostdin", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=180x320:r=24:d=4",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-threads", "2",
            "-shortest", str(cls.source),
        ], check=True, capture_output=True, timeout=30)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def plan(self, *, speech=False):
        plan = [{"clip_id": str(i), "path": str(self.source), "start": 0, "duration": 4,
                 "has_audio": i == 0, "ranking": {"rank": i + 1, "title": f"Motiv {i + 1}",
                 "reason": "Tydeleg motiv og fine fargar.", "criterion": "Visuelt inntrykk"},
                 **({"voiceover": "Sjå dei fine fargane."} if speech else {})} for i in range(2)]
        plan[0]["presentation"] = {"title": "Rangering", "ranking_direction": "best_first", "narration": speech}
        return plan

    def test_ranked_video_is_playable_with_overlay_and_correct_duration(self):
        output = self.root / "ranked.mp4"
        result = media.render(self.plan(), output, "9:16", "crop", "professional", lambda *_: None)
        self.assertAlmostEqual(result["duration"], 8, delta=0.3)
        self.assertEqual((result["width"], result["height"]), (720, 1280))
        pixels = subprocess.run([
            media._binary("ffmpeg"), "-v", "error", "-ss", "2", "-i", str(output),
            "-frames:v", "1", "-vf", "crop=500:250:0:0", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ], check=True, capture_output=True, timeout=30).stdout
        # Kvite/gule tekstpikslar må faktisk finnast i den elles blå kjelda.
        lit = sum(r > 160 and g > 120 for r, g in zip(pixels[0::3], pixels[1::3]))
        self.assertGreater(lit, 200)

    def test_model_text_is_written_as_data_not_filter_syntax(self):
        with TemporaryDirectory() as directory:
            plan = self.plan()
            text = "%{eif:1:d};[evil]"
            plan[0]["ranking"]["title"] = text
            filters = presentation.filters(plan, 0, 720, 1280, Path(directory))
            self.assertNotIn(text, ",".join(filters))
            self.assertTrue(all("expansion=none" in value for value in filters if value.startswith("drawtext=")))
            self.assertIn(text, "\n".join(p.read_text(encoding="utf-8") for p in Path(directory).glob("*.txt")))

    def test_progressive_scoreboard_reveals_only_shown_clips(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            plan = self.plan()
            presentation.filters(plan, 0, 720, 1280, path)
            first = "\n".join(p.read_text(encoding="utf-8") for p in path.glob("overlay-00-*.txt"))
            self.assertIn("Motiv 1", first)
            self.assertNotIn("Motiv 2", first)
            presentation.filters(plan, 1, 720, 1280, path)
            second = "\n".join(p.read_text(encoding="utf-8") for p in path.glob("overlay-01-*.txt"))
            self.assertIn("Motiv 1", second)
            self.assertIn("Motiv 2", second)

    def test_overlong_narration_fails_instead_of_cutting_off_words(self):
        output = self.root / "too-long.mp4"
        with patch("app.narration.synthesize", return_value={"duration": 30, "path": str(self.root / "unused.wav")}):
            with self.assertRaisesRegex(media.MediaError, "for lang"):
                media.render(self.plan(speech=True), output, "16:9", "pad", "professional", lambda *_: None)
        self.assertFalse(output.exists())

    @unittest.skipUnless(narration.availability()["available"], "Norsk lokal stemme manglar")
    def test_actual_norwegian_voice_mixes_with_sound_and_silent_sources(self):
        output = self.root / "narrated.mp4"
        result = media.render(self.plan(speech=True), output, "16:9", "pad", "energetic", lambda *_: None)
        self.assertAlmostEqual(result["duration"], 8, delta=0.3)
        self.assertTrue(result["has_audio"])
        samples = subprocess.run([
            media._binary("ffmpeg"), "-v", "error", "-ss", "4.3", "-t", "2",
            "-i", str(output), "-vn", "-ac", "1", "-f", "s16le", "-",
        ], check=True, capture_output=True, timeout=30).stdout
        self.assertTrue(any(samples), "Tale må høyrast òg når kjeldeklippet er utan lyd")


if __name__ == "__main__":
    unittest.main()
