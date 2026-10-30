"""Gemini-testar utan nettverk, ekte nøkkel eller betalte API-kall.

Google-klienten er alltid erstatta med ein liten lokal testdobbel. Berre
FFmpeg-kontrollen brukar eit ekte eksternt program, på syntetiske videoar.
"""

from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import gemini, media
from app.main import create_app


TEST_KEY = "test-placeholder-never-a-real-key"
PROVIDER_PRIVATE_TEXT = "provider-private-detail-should-never-be-shown"


class FakeFiles:
    """Den avgrensa delen av Files API som planlegginga treng."""

    def __init__(self, events):
        self.events = events
        self.uploads = []
        self.gets = []
        self.deletes = []
        self.upload_error = None
        self.delete_error = None
        self.delete_errors = []
        self.initial_state = "ACTIVE"
        self.polled_state = "ACTIVE"
        self.files = {}

    def file(self, name, state):
        return SimpleNamespace(
            name=name,
            uri="https://generativelanguage.googleapis.com/v1beta/" + name,
            mime_type="video/mp4",
            state=SimpleNamespace(name=state),
        )

    def upload(self, *, file, config):
        name = config.name
        self.uploads.append((file, config))
        self.events.append(("upload", name))
        if self.upload_error:
            raise self.upload_error
        uploaded = self.file(name, self.initial_state)
        self.files[name] = uploaded
        return uploaded

    def get(self, *, name, config=None):
        self.gets.append(name)
        return self.file(name, self.polled_state)

    def delete(self, *, name, config=None):
        self.deletes.append(name)
        self.events.append(("delete", name))
        if self.delete_errors:
            error = self.delete_errors.pop(0)
            if error:
                raise error
        if self.delete_error:
            raise self.delete_error
        self.files.pop(name, None)


class FakeModels:
    def __init__(self):
        self.calls = []
        self.error = None
        self.text = json.dumps({"segments": [
            {"clip_id": "clip-a", "start_seconds": 0, "end_seconds": 2, "order": 1},
        ]})

    def generate_content(self, **arguments):
        self.calls.append(arguments)
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.text)


class FakeClient:
    def __init__(self, events):
        self.files = FakeFiles(events)
        self.models = FakeModels()
        self.closed = False

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *arguments):
        self.close()


class NoNetworkCase(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.events = []
        self.pending = set()
        self.fake = FakeClient(self.events)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"GEMINI_API_KEY": TEST_KEY, "GOOGLE_API_KEY": "", "GEMINI_MODEL": "gemini-test-model"}).start()
        self.client_constructor = patch("app.gemini.genai.Client", return_value=self.fake).start()
        # Også ein utilsikta kodeveg skal feile, framfor å nå Google.
        patch("socket.socket.connect", side_effect=AssertionError("Nettverk er forbode i Gemini-testane.")).start()
        patch("app.gemini.POLL_INTERVAL_SECONDS", 0).start()
        self.clips = []
        for identifier in ("clip-a", "clip-b"):
            path = self.directory / f"{identifier}.mp4"
            path.write_bytes(b"Berre lokal planvalidering; ingen opplasting skjer.")
            self.clips.append({"id": identifier, "path": str(path), "duration": 5.0, "has_audio": False})

    def uploaded(self, name):
        self.events.append(("reserved", name))
        self.pending.add(name)

    def deleted(self, name):
        self.events.append(("forgotten", name))
        self.pending.discard(name)

    def generate(self, clips=None):
        return gemini.generate_plan(
            clips or self.clips, "Vel eit relevant utdrag.", 2, "professional", lambda *_: None,
            on_uploaded=self.uploaded, on_deleted=self.deleted,
        )

    @contextmanager
    def captured_logs(self):
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger()
        logger.addHandler(handler)
        try:
            yield stream
        finally:
            logger.removeHandler(handler)

    def assert_sanitized_failure(self):
        with self.captured_logs() as logs:
            with self.assertRaises(gemini.GeminiError) as raised:
                self.generate(clips=self.clips[:1])
        for private in (TEST_KEY, PROVIDER_PRIVATE_TEXT):
            self.assertNotIn(private, str(raised.exception))
            self.assertNotIn(private, logs.getvalue())
        self.assertIsInstance(raised.exception.code, str)
        self.assertTrue(raised.exception.code)
        return raised.exception


class GeminiPlanTests(NoNetworkCase):
    def test_configuration_checks_presence_without_making_requests(self):
        self.assertTrue(gemini.configured())
        self.assertEqual(gemini.model_name(), "gemini-test-model")
        with patch.dict(os.environ, {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}):
            self.assertFalse(gemini.configured())
            with self.assertRaises(gemini.GeminiError):
                self.generate()
        self.client_constructor.assert_not_called()

    def test_valid_plan_orders_segments_and_uses_only_server_paths(self):
        text = json.dumps({"segments": [
            {"clip_id": "clip-a", "start_seconds": 1, "end_seconds": 2.5, "order": 2},
            {"clip_id": "clip-b", "start_seconds": 0.5, "end_seconds": 2, "order": 1},
        ]})
        plan = gemini.parse_plan(text, self.clips, 3)
        self.assertEqual([segment["clip_id"] for segment in plan], ["clip-b", "clip-a"])
        self.assertEqual([segment["path"] for segment in plan], [self.clips[1]["path"], self.clips[0]["path"]])
        self.assertEqual([segment["start"] for segment in plan], [0.5, 1.0])
        self.assertEqual([segment["duration"] for segment in plan], [1.5, 1.5])
        media.validate_plan(plan, self.clips, 3)
        self.client_constructor.assert_not_called()

    def test_malformed_or_unsafe_plans_are_rejected(self):
        segment = {"clip_id": "clip-a", "start_seconds": 0, "end_seconds": 2, "order": 1}
        bad_responses = [
            "ikkje JSON", "[]", "{}", '{"segments": []}',
            json.dumps({"segments": [{**segment, "clip_id": "ukjent-klipp"}]}),
            json.dumps({"segments": [{**segment, "start_seconds": -1}]}),
            json.dumps({"segments": [{**segment, "end_seconds": 6}]}),
            json.dumps({"segments": [{**segment, "end_seconds": 0}]}),
            json.dumps({"segments": [{**segment, "start_seconds": True}]}),
            json.dumps({"segments": [{**segment, "start_seconds": "0"}]}),
            json.dumps({"segments": [{**segment, "start_seconds": float("nan")}]}),
            json.dumps({"segments": [{**segment, "end_seconds": float("inf")}]}),
            json.dumps({"segments": [{**segment, "order": True}]}),
            json.dumps({"segments": [{**segment, "order": "1"}]}),
            json.dumps({"segments": [{**segment, "order": 1.5}]}),
            json.dumps({"segments": [{**segment, "order": 0}]}),
            json.dumps({"segments": [{**segment, "order": 2}]}),
            json.dumps({"segments": [{**segment, "end_seconds": 1}]}),
            json.dumps({"segments": [
                {**segment, "end_seconds": 1},
                {**segment, "clip_id": "clip-b", "end_seconds": 1},
            ]}),
            json.dumps({"segments": [
                {**segment, "end_seconds": 2 / 13, "order": order}
                for order in range(1, 14)
            ]}),
            '{"segments": [], "segments": ' + json.dumps([segment]) + '}',
        ]
        for text in bad_responses:
            with self.subTest(response=text):
                with self.assertRaises(media.MediaError):
                    gemini.parse_plan(text, self.clips, 2)
        self.client_constructor.assert_not_called()

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg må vere installert")
    def test_validated_ai_plan_produces_playable_video_without_an_sdk_call(self):
        for clip, colour in zip(self.clips, ("red", "blue")):
            subprocess.run([
                "ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
                f"color=c={colour}:s=160x90:r=24:d=1", "-c:v", "libx264", "-threads", "2",
                "-pix_fmt", "yuv420p", clip["path"],
            ], check=True, capture_output=True, timeout=30)
            clip["duration"] = 1
        plan = gemini.parse_plan(json.dumps({"segments": [
            {"clip_id": "clip-b", "start_seconds": 0.25, "end_seconds": 0.75, "order": 1},
            {"clip_id": "clip-a", "start_seconds": 0.25, "end_seconds": 0.75, "order": 2},
        ]}), self.clips, 1)
        output = self.directory / "eksport.mp4"
        result = media.render(plan, output, "16:9", "pad", "energetic", lambda *_: None)
        self.assertAlmostEqual(result["duration"], 1, delta=0.2)
        self.assertEqual((result["width"], result["height"]), (1280, 720))
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(output), "-f", "null", "-"],
                       check=True, capture_output=True, timeout=30)
        self.client_constructor.assert_not_called()


class GeminiSdkTests(NoNetworkCase):
    def test_uploads_and_generation_happen_once_and_remote_files_are_cleaned(self):
        self.fake.files.initial_state = "PROCESSING"
        plan = self.generate()
        self.assertEqual(len(plan), 1)
        self.assertEqual(len(self.fake.files.uploads), len(self.clips))
        self.assertEqual(len(self.fake.files.gets), len(self.clips))
        self.assertEqual(len(self.fake.models.calls), 1)
        names = [config.name for _, config in self.fake.files.uploads]
        self.assertEqual(set(self.fake.files.deletes), set(names))
        self.assertFalse(self.pending)
        for name in names:
            self.assertRegex(name, r"^files/[a-f0-9]{32}$")
            self.assertLess(self.events.index(("reserved", name)), self.events.index(("upload", name)))
            self.assertLess(self.events.index(("delete", name)), self.events.index(("forgotten", name)))
        http_options = self.client_constructor.call_args.kwargs["http_options"]
        self.assertEqual(http_options.retry_options.attempts, 1)
        self.assertGreater(http_options.timeout, 0)
        self.assertLessEqual(http_options.timeout, 120_000)
        config = self.fake.models.calls[0]["config"]
        self.assertEqual(config.response_mime_type, "application/json")
        self.assertTrue(config.response_json_schema)

    def test_rate_limit_does_not_retry_generation_and_sanitizes_provider_details(self):
        self.fake.models.error = gemini.errors.ClientError(429, {"error": {
            "message": PROVIDER_PRIVATE_TEXT + TEST_KEY, "status": "RESOURCE_EXHAUSTED",
        }})
        self.assert_sanitized_failure()
        self.assertEqual(len(self.fake.models.calls), 1)
        self.assertEqual(len(self.fake.files.uploads), 1)
        self.assertEqual(len(self.fake.files.deletes), 1)
        self.assertFalse(self.pending)

    def test_timeout_does_not_retry_generation_and_cleans_files(self):
        self.fake.models.error = TimeoutError(PROVIDER_PRIVATE_TEXT + TEST_KEY)
        self.assert_sanitized_failure()
        self.assertEqual(len(self.fake.models.calls), 1)
        self.assertEqual(len(self.fake.files.deletes), 1)
        self.assertFalse(self.pending)

    def test_upload_timeout_still_cleans_the_name_reserved_before_upload(self):
        self.fake.files.upload_error = TimeoutError(PROVIDER_PRIVATE_TEXT + TEST_KEY)
        self.assert_sanitized_failure()
        self.assertEqual(len(self.fake.files.uploads), 1)
        self.assertEqual(len(self.fake.models.calls), 0)
        self.assertEqual(len(self.fake.files.deletes), 1)
        name = self.fake.files.uploads[0][1].name
        self.assertLess(self.events.index(("reserved", name)), self.events.index(("upload", name)))
        self.assertFalse(self.pending)

    def test_invalid_model_response_is_not_repaired_by_another_paid_call(self):
        self.fake.models.text = '{"segments": [{"clip_id": "ukjent"}]}'
        self.assert_sanitized_failure()
        self.assertEqual(len(self.fake.models.calls), 1)
        self.assertEqual(len(self.fake.files.deletes), 1)
        self.assertFalse(self.pending)

    def test_file_processing_polling_is_bounded_and_cleans_up(self):
        self.fake.files.initial_state = self.fake.files.polled_state = "PROCESSING"
        self.assert_sanitized_failure()
        self.assertGreater(len(self.fake.files.gets), 0)
        self.assertLessEqual(len(self.fake.files.gets), gemini.MAX_FILE_POLLS)
        self.assertEqual(len(self.fake.models.calls), 0)
        self.assertEqual(len(self.fake.files.uploads), 1)
        self.assertEqual(len(self.fake.files.deletes), 1)
        self.assertFalse(self.pending)

    def test_failed_file_analysis_is_not_sent_to_generation(self):
        self.fake.files.initial_state = "FAILED"
        self.assert_sanitized_failure()
        self.assertEqual(len(self.fake.models.calls), 0)
        self.assertEqual(len(self.fake.files.deletes), 1)
        self.assertFalse(self.pending)

    def test_delete_can_retry_transient_errors_without_repeating_generation(self):
        self.fake.files.delete_errors = [gemini.errors.ServerError(503, {"error": {"message": PROVIDER_PRIVATE_TEXT}})]
        plan = self.generate(clips=self.clips[:1])
        self.assertEqual(len(plan), 1)
        self.assertEqual(len(self.fake.files.deletes), 2)
        self.assertEqual(len(self.fake.models.calls), 1)
        self.assertEqual(len(self.fake.files.uploads), 1)
        self.assertFalse(self.pending)

    def test_unresolved_remote_cleanup_retains_reference_and_fails_job(self):
        self.fake.files.delete_error = gemini.errors.ClientError(403, {"error": {"message": PROVIDER_PRIVATE_TEXT + TEST_KEY}})
        self.assert_sanitized_failure()
        self.assertEqual(len(self.fake.models.calls), 1)
        self.assertTrue(self.pending)
        self.assertFalse(any(event == "forgotten" for event, _ in self.events))

    def test_cleanup_of_an_already_deleted_file_is_successful(self):
        self.fake.files.delete_error = gemini.errors.ClientError(404, {"error": {"message": "Not found"}})
        name = "files/" + "a" * 32
        self.pending.add(name)
        unresolved = gemini.cleanup_files([name], on_deleted=self.deleted)
        self.assertEqual(unresolved, [])
        self.assertFalse(self.pending)
        self.assertEqual(self.fake.files.deletes, [name])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg må vere installert")
class GeminiApiTests(NoNetworkCase):
    def setUp(self):
        super().setUp()
        self.data_directory = self.directory / "data"
        self.api = None
        self.addCleanup(self.close_api)
        self.open_api()
        self.source = self.directory / "syntetisk.mp4"
        subprocess.run([
            "ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
            "color=c=blue:s=160x90:r=24:d=1", "-c:v", "libx264", "-threads", "2",
            "-pix_fmt", "yuv420p", str(self.source),
        ], check=True, capture_output=True, timeout=30)
        response = self.api.post("/api/projects", json={"name": "AI-test utan API-kostnad"})
        self.assertEqual(response.status_code, 201, response.text)
        self.project_id = response.json()["id"]
        with self.source.open("rb") as source:
            response = self.api.post(
                f"/api/projects/{self.project_id}/clips",
                files={"file": (self.source.name, source, "video/mp4")},
            )
        self.assertEqual(response.status_code, 201, response.text)

    def open_api(self):
        self.api = TestClient(create_app(data_dir=self.data_directory))
        self.api.__enter__()

    def close_api(self):
        if self.api is not None:
            self.api.__exit__(None, None, None)
            self.api = None

    def settings(self, **changes):
        settings = {
            "description": "Finn eit relevant blått utdrag.", "duration_seconds": 1,
            "aspect": "16:9", "fit": "pad", "style": "energetic", "subtitles": False,
            "mode": "gemini", "ai_consent": True,
        }
        settings.update(changes)
        return settings

    def submit(self, **changes):
        return self.api.post(f"/api/projects/{self.project_id}/jobs", json=self.settings(**changes))

    def await_job(self, job_id):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            response = self.api.get(f"/api/jobs/{job_id}")
            self.assertEqual(response.status_code, 200, response.text)
            job = response.json()
            if job["status"] in ("completed", "failed"):
                return job
            time.sleep(0.05)
        self.fail("Gemini-testjobben vart ikkje ferdig innan 30 sekund.")

    def test_gemini_requires_explicit_consent_and_a_configured_key(self):
        self.assertTrue(self.api.get("/api/config").json()["ai_available"])
        response = self.submit(ai_consent=False)
        self.assertIn(response.status_code, (400, 403, 409, 422), response.text)
        without_consent = self.settings()
        without_consent.pop("ai_consent")
        response = self.api.post(f"/api/projects/{self.project_id}/jobs", json=without_consent)
        self.assertIn(response.status_code, (400, 403, 409, 422), response.text)
        with patch.dict(os.environ, {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}):
            self.assertFalse(self.api.get("/api/config").json()["ai_available"])
            response = self.submit()
            self.assertIn(response.status_code, (400, 409, 422, 503), response.text)
        self.assertEqual(self.api.get(f"/api/projects/{self.project_id}").json()["jobs"], [])
        # Ein konfigurert nøkkel skal ikkje gjere enkel redigering til eit API-kall.
        response = self.submit(mode="basic", ai_consent=False)
        self.assertIn(response.status_code, (200, 201, 202), response.text)
        self.assertEqual(self.await_job(response.json()["id"])["status"], "completed")
        self.client_constructor.assert_not_called()

    def test_mocked_gemini_plan_renders_and_job_instruction_is_immutable(self):
        received_descriptions = []
        received_models = []

        def local_plan(clips, description, duration_seconds, style, progress, **callbacks):
            received_descriptions.append(description)
            received_models.append(callbacks.get("model"))
            return media.build_plan(clips, duration_seconds, style)

        original_description = "Vel det blå klippet til ein roleg skulevideo."
        with patch("app.main.gemini.generate_plan", side_effect=local_plan) as generate:
            response = self.submit(description=original_description)
            self.assertIn(response.status_code, (200, 201, 202), response.text)
            first = self.await_job(response.json()["id"])
            self.assertEqual(first["status"], "completed", first)
            self.assertEqual(received_descriptions, [original_description])
            self.assertEqual(received_models, ["gemini-test-model"])
            self.assertEqual(first["settings"]["description"], original_description)
            self.assertEqual(first["settings"]["gemini_model"], "gemini-test-model")
            self.assertEqual(first["settings"]["mode"], "gemini")
            self.assertEqual(self.api.get(f"/api/jobs/{first['id']}/download").status_code, 200)

            new_description = "Ny tekst for ein annan eksport."
            response = self.submit(mode="basic", ai_consent=False, description=new_description)
            self.assertIn(response.status_code, (200, 201, 202), response.text)
            self.assertEqual(self.await_job(response.json()["id"])["status"], "completed")
            project = self.api.get(f"/api/projects/{self.project_id}").json()
            self.assertEqual(project["description"], new_description)
            restored_first = self.api.get(f"/api/jobs/{first['id']}").json()
            self.assertEqual(restored_first["settings"]["description"], original_description)
            self.assertEqual(generate.call_count, 1)
            self.assertNotIn(TEST_KEY, json.dumps(restored_first))
        self.client_constructor.assert_not_called()

    def test_provider_failure_persists_without_basic_fallback_or_automatic_retry(self):
        self.fake.models.error = gemini.errors.ClientError(429, {"error": {
            "message": PROVIDER_PRIVATE_TEXT + TEST_KEY, "status": "RESOURCE_EXHAUSTED",
        }})
        with self.captured_logs() as logs:
            response = self.submit()
            self.assertIn(response.status_code, (200, 201, 202), response.text)
            job = self.await_job(response.json()["id"])
        self.assertEqual(job["status"], "failed", job)
        self.assertEqual(job["settings"]["mode"], "gemini")
        self.assertTrue(job["error"])
        self.assertIn(self.api.get(f"/api/jobs/{job['id']}/download").status_code, (404, 409))
        for private in (TEST_KEY, PROVIDER_PRIVATE_TEXT):
            self.assertNotIn(private, json.dumps(job))
            self.assertNotIn(private, logs.getvalue())
        self.assertEqual(len(self.fake.models.calls), 1)
        self.assertEqual(len(self.fake.files.uploads), 1)
        self.assertEqual(len(self.fake.files.deletes), 1)
        self.close_api()
        self.open_api()
        restored = self.api.get(f"/api/jobs/{job['id']}").json()
        self.assertEqual(restored["status"], "failed")
        self.assertEqual(restored["error"], job["error"])
        self.assertEqual(len(self.fake.models.calls), 1, "Omstart skal ikkje prøve ein betalt analyse på nytt")

    def test_failed_remote_cleanup_is_persisted_and_retried_on_restart_without_analysis(self):
        self.fake.models.error = gemini.errors.ClientError(429, {"error": {"message": PROVIDER_PRIVATE_TEXT}})
        self.fake.files.delete_error = gemini.errors.ClientError(403, {"error": {"message": PROVIDER_PRIVATE_TEXT}})
        response = self.submit()
        self.assertIn(response.status_code, (200, 201, 202), response.text)
        job = self.await_job(response.json()["id"])
        self.assertEqual(job["status"], "failed", job)
        self.close_api()

        def remote_names():
            # Det finst med vilje ikkje eit API som eksponerer Google-filreferansar.
            connection = sqlite3.connect(self.data_directory / "app.db")
            try:
                return [row[0] for row in connection.execute("SELECT file_name FROM gemini_files")]
            finally:
                connection.close()

        reserved = [config.name for _, config in self.fake.files.uploads]
        self.assertEqual(remote_names(), reserved)
        self.fake.files.delete_error = None
        self.open_api()
        deadline = time.monotonic() + 5
        while remote_names() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(remote_names(), [])
        self.assertEqual(len(self.fake.files.uploads), 1)
        self.assertEqual(len(self.fake.models.calls), 1)
        self.assertEqual(self.fake.files.deletes, reserved + reserved)
        self.assertEqual(self.api.get(f"/api/jobs/{job['id']}").json()["status"], "failed")


if __name__ == "__main__":
    unittest.main()
