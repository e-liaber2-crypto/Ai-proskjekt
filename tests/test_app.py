"""Integrasjonstestar med ekte, små videofiler. Køyr med unittest discover."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import shutil
import sqlite3
import subprocess
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app import media


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg og ffprobe må vere installerte")
class VideoWorkflowTests(unittest.TestCase):
    """Testar den offentlege API-flyten, ikkje berre hjelpefunksjonane."""

    @classmethod
    def setUpClass(cls):
        cls.fixture_dir = TemporaryDirectory()
        root = Path(cls.fixture_dir.name)
        cls.red_clip = root / "raud-med-lyd.mp4"
        cls.blue_clip = root / "blaa-utan-lyd.mp4"
        cls.audio_only = root / "berre-lyd.mp4"
        cls.long_clip = root / "lang-video.mp4"
        cls.ffmpeg(
            "-f", "lavfi", "-i", "color=c=red:s=320x180:r=24:d=1.3",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1.3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-shortest", str(cls.red_clip),
        )
        cls.ffmpeg(
            "-f", "lavfi", "-i", "color=c=blue:s=180x320:r=24:d=1.3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(cls.blue_clip),
        )
        cls.ffmpeg(
            "-f", "lavfi", "-i", "sine=duration=1:sample_rate=48000",
            "-c:a", "aac", str(cls.audio_only),
        )
        cls.ffmpeg(
            "-f", "lavfi", "-i", "color=c=green:s=320x180:r=24:d=61",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-an", "-threads", "2", str(cls.long_clip),
        )

    @classmethod
    def tearDownClass(cls):
        cls.fixture_dir.cleanup()

    @staticmethod
    def ffmpeg(*arguments):
        return subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
             "-threads", "2", "-filter_threads", "1", *arguments],
            check=True, capture_output=True, timeout=60,
        )

    def setUp(self):
        self.data_dir = TemporaryDirectory()
        self.client = TestClient(create_app(data_dir=Path(self.data_dir.name)))
        self.client.__enter__()

    def tearDown(self):
        if self.client is not None:
            self.client.__exit__(None, None, None)
        self.data_dir.cleanup()

    def assert_rejected(self, response):
        self.assertIn(response.status_code, (400, 404, 409, 413, 415, 422), response.text)
        self.assertIn("detail", response.json(), response.text)

    def project(self, name="Skulevideo"):
        response = self.client.post("/api/projects", json={"name": name})
        self.assertIn(response.status_code, (200, 201), response.text)
        return response.json()["id"]

    def upload(self, project_id, path):
        with path.open("rb") as video:
            response = self.client.post(
                f"/api/projects/{project_id}/clips",
                files={"file": (path.name, video, "video/mp4")},
            )
        self.assertIn(response.status_code, (200, 201), response.text)
        return response.json()

    def populated_project(self):
        project_id = self.project()
        self.upload(project_id, self.red_clip)
        self.upload(project_id, self.blue_clip)
        return project_id

    @staticmethod
    def settings(**overrides):
        settings = {
            "description": "Set saman dei to klippa til ein skulevideo.",
            "duration_seconds": 2,
            "aspect": "16:9",
            "fit": "pad",
            "style": "professional",
            "subtitles": False,
        }
        settings.update(overrides)
        return settings

    def submit(self, project_id, **settings):
        response = self.client.post(
            f"/api/projects/{project_id}/jobs", json=self.settings(**settings),
        )
        self.assertIn(response.status_code, (200, 201, 202), response.text)
        return response.json()

    def await_job(self, job_id):
        deadline = time.monotonic() + 60
        observed_states = []
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/jobs/{job_id}")
            self.assertEqual(response.status_code, 200, response.text)
            job = response.json()
            observed_states.append(job["status"])
            self.assertIn(job["status"], ("queued", "processing", "completed", "failed"))
            self.assertGreaterEqual(job["progress"], 0)
            self.assertLessEqual(job["progress"], 100)
            if job["status"] in ("completed", "failed"):
                return job
            time.sleep(0.05)
        self.fail(f"Jobben vart ikkje ferdig innan 60 sekund. Observerte statusar: {observed_states[-10:]}")

    def completed_job(self, project_id, **settings):
        job = self.await_job(self.submit(project_id, **settings)["id"])
        self.assertEqual(job["status"], "completed", job)
        self.assertEqual(job["progress"], 100)
        return job

    def test_real_exports_all_formats_with_and_without_audio(self):
        project_id = self.populated_project()
        formats = [
            ("16:9", "pad", "professional", 1280, 720),
            ("9:16", "crop", "energetic", 720, 1280),
            ("1:1", "pad", "calm", 720, 720),
        ]
        for aspect, fit, style, width, height in formats:
            with self.subTest(aspect=aspect, fit=fit, style=style):
                job = self.completed_job(project_id, aspect=aspect, fit=fit, style=style)
                response = self.client.get(f"/api/jobs/{job['id']}/download")
                self.assertEqual(response.status_code, 200, response.text[:200])
                self.assertIn("attachment", response.headers["content-disposition"])
                self.assertIn("video/mp4", response.headers["content-type"])
                self.assertIn(b"ftyp", response.content[:32])
                output = Path(self.data_dir.name) / f"checked-{aspect.replace(':', '-')}.mp4"
                output.write_bytes(response.content)
                information = json.loads(subprocess.check_output(
                    ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)],
                    timeout=30,
                ))
                video = next(s for s in information["streams"] if s["codec_type"] == "video")
                audio = next(s for s in information["streams"] if s["codec_type"] == "audio")
                self.assertEqual((video["width"], video["height"]), (width, height))
                self.assertEqual(video["codec_name"], "h264")
                self.assertEqual(video["pix_fmt"], "yuv420p")
                self.assertEqual(audio["codec_name"], "aac")
                self.assertAlmostEqual(float(information["format"]["duration"]), 2, delta=0.25)
                self.assertAlmostEqual(job["output_duration"], 2, delta=0.25)
                self.assertEqual((job["output_width"], job["output_height"]), (width, height))
                self.ffmpeg("-i", str(output), "-f", "null", "-")
                # Begge kjeldene skal finnast i resultatet, ikkje berre ei gyldig tom fil.
                colours = []
                for timestamp in (0.5, 1.5):
                    pixel = self.ffmpeg(
                        "-ss", str(timestamp), "-i", str(output), "-frames:v", "1",
                        "-vf", "crop=2:2:iw/2:ih/2,scale=1:1", "-f", "rawvideo",
                        "-pix_fmt", "rgb24", "pipe:1",
                    ).stdout
                    self.assertEqual(len(pixel), 3)
                    colours.append(pixel)
                self.assertGreater(colours[0][0], colours[0][2] + 60, colours)
                self.assertGreater(colours[1][2], colours[1][0] + 60, colours)
                preview = self.client.get(f"/api/jobs/{job['id']}/video", headers={"Range": "bytes=0-31"})
                self.assertEqual(preview.status_code, 206)
                self.assertEqual(preview.content, response.content[:32])
        usage = self.client.get("/api/usage").json()
        self.assertGreater(usage["source_bytes"], 0)
        self.assertGreater(usage["output_bytes"], 0)
        self.assertGreaterEqual(usage["rendered_seconds"], 5.5)

    def test_long_video_can_export_more_than_one_minute(self):
        project_id = self.project("Ein heil video på 61 sekund")
        clip = self.upload(project_id, self.long_clip)
        self.assertGreaterEqual(clip["duration"], 61)
        job = self.completed_job(
            project_id, duration_seconds=61, aspect="16:9", fit="pad", style="energetic",
        )
        self.assertAlmostEqual(job["output_duration"], 61, delta=0.25)
        response = self.client.get(f"/api/jobs/{job['id']}/download")
        self.assertEqual(response.status_code, 200)
        output = Path(self.data_dir.name) / "checked-long-video.mp4"
        output.write_bytes(response.content)
        information = json.loads(subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)],
            timeout=30,
        ))
        self.assertAlmostEqual(float(information["format"]["duration"]), 61, delta=0.25)
        video = next(stream for stream in information["streams"] if stream["codec_type"] == "video")
        self.assertEqual((video["width"], video["height"]), (1280, 720))
        # Dekod heile fila, også slutten etter det første minuttet.
        self.ffmpeg("-i", str(output), "-f", "null", "-")
        self.assertGreaterEqual(self.client.get("/api/usage").json()["rendered_seconds"], 61)

    def test_invalid_uploads_are_rejected_without_adding_clips(self):
        project_id = self.project()
        endpoint = f"/api/projects/{project_id}/clips"
        for name, contents, content_type in [
            ("ikkje-video.mp4", b"Dette er berre tekst.", "video/mp4"),
            ("program.exe", self.red_clip.read_bytes(), "application/octet-stream"),
            ("tom.mp4", b"", "video/mp4"),
            ("berre-lyd.mp4", self.audio_only.read_bytes(), "video/mp4"),
        ]:
            with self.subTest(name=name):
                self.assert_rejected(self.client.post(endpoint, files={"file": (name, contents, content_type)}))
        config = self.client.get("/api/config").json()
        maximum = config["limits"]["max_upload_mb"]
        oversized = self.client.post(
            endpoint,
            files={"file": ("stor.mp4", self.red_clip.read_bytes(), "video/mp4")},
            headers={"Content-Length": str((maximum + 2) * 1024 * 1024)},
        )
        self.assertEqual(oversized.status_code, 413, oversized.text)
        self.assertEqual(self.client.get(f"/api/projects/{project_id}").json()["clips"], [])

    def test_missing_material_and_invalid_settings(self):
        project_id = self.project()
        endpoint = f"/api/projects/{project_id}/jobs"
        self.assert_rejected(self.client.post(endpoint, json=self.settings()))
        self.upload(project_id, self.red_clip)
        self.assert_rejected(self.client.post(endpoint, json=self.settings(duration_seconds=60)))
        for invalid in [
            {"duration_seconds": 0}, {"duration_seconds": 601},
            {"duration_seconds": 1.5}, {"aspect": "3:2"}, {"fit": "stretch"},
            {"style": "unknown"}, {"subtitles": True},
        ]:
            with self.subTest(settings=invalid):
                self.assert_rejected(self.client.post(endpoint, json=self.settings(**invalid)))
        self.assertEqual(self.client.get(f"/api/projects/{project_id}").json()["jobs"], [])

    def test_background_failure_is_reported(self):
        project_id = self.populated_project()
        with patch("app.main.media.render", side_effect=media.MediaError("Videomotoren kunne ikkje fullføre testen.")):
            job = self.await_job(self.submit(project_id)["id"])
        self.assertEqual(job["status"], "failed", job)
        self.assertTrue(job["error"], job)
        self.assert_rejected(self.client.get(f"/api/jobs/{job['id']}/download"))
        # Ein feila jobb skal ikkje blokkere køen for neste prosjekt.
        retry = self.completed_job(project_id)
        self.assertNotEqual(retry["id"], job["id"])

    def test_project_and_output_survive_restart_and_schedules_are_local(self):
        project_id = self.populated_project()
        job = self.completed_job(project_id)
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        schedule = {
            "job_id": job["id"], "channel": "YouTube", "scheduled_at": future,
            "timezone": "Europe/Oslo", "approved": True,
        }
        for invalid in [
            {"approved": False},
            {"scheduled_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()},
            {"scheduled_at": "2030-01-01T12:00:00"},
            {"timezone": "Ikkje/EiTidssone"},
        ]:
            with self.subTest(schedule=invalid):
                self.assert_rejected(self.client.post("/api/schedules", json={**schedule, **invalid}))
        first = self.client.post("/api/schedules", json=schedule)
        self.assertIn(first.status_code, (200, 201), first.text)
        self.assertEqual(first.json()["status"], "planned_local")
        duplicate = self.client.post("/api/schedules", json=schedule)
        self.assertIn(duplicate.status_code, (200, 201), duplicate.text)
        self.assertEqual(first.json()["id"], duplicate.json()["id"])
        self.assertEqual(len(self.client.get("/api/schedules").json()), 1)

        self.client.__exit__(None, None, None)
        self.client = TestClient(create_app(data_dir=Path(self.data_dir.name)))
        self.client.__enter__()
        restored = self.client.get(f"/api/projects/{project_id}")
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(len(restored.json()["clips"]), 2)
        self.assertEqual(restored.json()["latest_job"]["status"], "completed")
        self.assertEqual(self.client.get(f"/api/jobs/{job['id']}/download").status_code, 200)
        self.assertEqual(self.client.get("/api/schedules").json()[0]["id"], first.json()["id"])
        deletion = self.client.delete(f"/api/schedules/{first.json()['id']}")
        self.assertEqual(deletion.status_code, 200)
        self.assertEqual(self.client.get("/api/schedules").json(), [])

    def test_project_validation_and_deletion(self):
        for invalid_name in ("", "   ", "x" * 201):
            self.assert_rejected(self.client.post("/api/projects", json={"name": invalid_name}))
        project_id = self.project("<script>Dette skal vere tekst</script>")
        clip = self.upload(project_id, self.red_clip)
        projects = self.client.get("/api/projects").json()
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["name"], "<script>Dette skal vere tekst</script>")
        deletion = self.client.delete(f"/api/projects/{project_id}/clips/{clip['id']}")
        self.assertEqual(deletion.status_code, 200)
        self.assertEqual(self.client.get(f"/api/projects/{project_id}").json()["clips"], [])
        self.assertEqual(self.client.delete(f"/api/projects/{project_id}").status_code, 200)
        self.assertEqual(self.client.get("/api/projects").json(), [])
        self.assertEqual(self.client.get(f"/api/projects/{project_id}").status_code, 404)

    def test_restart_marks_interrupted_job_failed_cleans_files_and_resumes_queue(self):
        project_id = self.populated_project()
        directory = Path(self.data_dir.name)
        self.client.__exit__(None, None, None)
        self.client = None
        interrupted_id, queued_id = uuid4().hex, uuid4().hex
        # Ein hardstopp kan ikkje simulerast med vanleg API-avslutning: ho ventar på
        # arbeidaren. Lag difor tilstanden prosessen ville etterlate i databasen.
        connection = sqlite3.connect(directory / "app.db")
        try:
            with connection:
                connection.executemany(
                    "INSERT INTO jobs(id,project_id,status,stage,progress,settings,created_at) VALUES(?,?,?,?,?,?,?)",
                    [
                        (interrupted_id, project_id, "processing", "Eksporterer", 45,
                         json.dumps(self.settings()), datetime.now(timezone.utc).isoformat()),
                        (queued_id, project_id, "queued", "I kø", 0,
                         json.dumps(self.settings()), datetime.now(timezone.utc).isoformat()),
                    ],
                )
        finally:
            connection.close()
        partial_output = directory / "outputs" / interrupted_id
        partial_output.mkdir()
        (partial_output / "video.mp4").write_bytes(b"avbroten, ufullstendig eksport")
        (partial_output / "segment.mp4").write_bytes(b"mellombels videodel")
        partial_upload = directory / "temporary" / "avbroten-opplasting"
        partial_upload.write_bytes(b"ufullstendig opplasting")

        self.client = TestClient(create_app(data_dir=directory))
        self.client.__enter__()
        interrupted = self.client.get(f"/api/jobs/{interrupted_id}")
        self.assertEqual(interrupted.status_code, 200)
        self.assertEqual(interrupted.json()["status"], "failed")
        self.assertTrue(interrupted.json()["error"])
        self.assertFalse(partial_output.exists(), "Restar frå avbroten eksport må ryddast")
        self.assertFalse(partial_upload.exists(), "Avbroten opplasting må ryddast")
        self.assert_rejected(self.client.get(f"/api/jobs/{interrupted_id}/download"))
        resumed = self.await_job(queued_id)
        self.assertEqual(resumed["status"], "completed", resumed)
        self.assertAlmostEqual(resumed["output_duration"], 2, delta=0.25)
        self.assertEqual(self.client.get(f"/api/jobs/{queued_id}/download").status_code, 200)


if __name__ == "__main__":
    unittest.main()
