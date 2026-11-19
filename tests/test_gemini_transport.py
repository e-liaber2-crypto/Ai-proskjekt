"""Sjekk det verkelege Google-SDK-et med ein heilt lokal HTTP-transport."""

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from google import genai
import httpx

from app import gemini, media
from app.main import create_app


class GeminiTransportTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg må vere installert")
    def test_render_retries_with_flash_lite_and_exports_geminis_cut_order(self):
        requests = []
        clip_ids = []

        def transport(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(503, json={"error": {"code": 503, "status": "UNAVAILABLE"}})
            plan = {"segments": [
                {"clip_id": clip_ids[1], "start_seconds": 0.5, "end_seconds": 1.5, "order": 1,
                 "caption": "Raud opning"},
                {"clip_id": clip_ids[0], "start_seconds": 0.5, "end_seconds": 1.48, "order": 2,
                 "caption": "Blå avslutning"},
            ]}
            return httpx.Response(200, json={"candidates": [{
                "content": {"role": "model", "parts": [{"text": json.dumps(plan)}]},
                "finishReason": "STOP",
            }]})

        render_env = {
            "RENDER": "true", "RENDER_EXTERNAL_HOSTNAME": "demo.onrender.com", "PUBLIC_HOST": "",
            "DEMO_PASSWORD": "test-password-for-render", "FFMPEG_THREADS": "1",
            "GEMINI_API_KEY": "offline-test-key", "GEMINI_MODEL": gemini.DEFAULT_MODEL,
        }
        with TemporaryDirectory() as temporary, patch.dict(os.environ, render_env):
            directory = Path(temporary)
            options = gemini._http_options()
            options.httpx_client = httpx.Client(transport=httpx.MockTransport(transport), trust_env=False)
            options.async_client_args = {"trust_env": False}
            client = genai.Client(api_key="offline-test-key", vertexai=False, http_options=options)
            with patch("app.gemini._client", return_value=client), patch(
                "app.gemini.ANALYSIS_RETRY_DELAYS", (0, 0),
            ), patch("app.gemini.uniform", return_value=0), TestClient(
                create_app(data_dir=directory / "data"), base_url="https://demo.onrender.com",
            ) as api:
                api.auth = ("laerar", "test-password-for-render")
                self.assertEqual(api.get("/api/config").json()["gemini_input"], "frames")
                project = api.post("/api/projects", json={"name": "To klipp på Render"})
                self.assertEqual(project.status_code, 201, project.text)
                project_path = f"/api/projects/{project.json()['id']}"
                for color in ("blue", "red"):
                    source = directory / f"{color}.mp4"
                    subprocess.run([
                        "ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                        f"color=c={color}:s=160x90:r=24:d=2",
                        "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", str(source),
                    ], check=True, capture_output=True, timeout=30)
                    with source.open("rb") as file:
                        uploaded = api.post(project_path + "/clips", files={"file": (source.name, file, "video/mp4")})
                    self.assertEqual(uploaded.status_code, 201, uploaded.text)
                    clip_ids.append(uploaded.json()["id"])

                accepted = api.post(project_path + "/jobs", json={
                    "description": "Bruk klippet med raud farge først, og det blå etterpå.",
                    "duration_seconds": 2, "style": "energetic", "mode": "gemini", "ai_consent": True,
                    "clip_labels": True,
                })
                self.assertEqual(accepted.status_code, 202, accepted.text)
                job_path = f"/api/jobs/{accepted.json()['id']}"
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    job = api.get(job_path).json()
                    if job["status"] in ("completed", "failed"):
                        break
                    time.sleep(0.1)
                self.assertEqual(job["status"], "completed", job)
                self.assertIsNone(job["fallback_reason"], job)
                self.assertEqual([part["clip_id"] for part in job["plan"]], clip_ids[::-1])
                self.assertEqual([part["start_seconds"] for part in job["plan"]], [0.5, 0.5])
                self.assertAlmostEqual(job["plan"][-1]["end_seconds"], 1.5)
                self.assertEqual([part["caption"] for part in job["plan"]], ["Raud opning", "Blå avslutning"])
                download = api.get(job_path + "/download")
                self.assertEqual(download.status_code, 200)
                output = directory / "gemini.mp4"
                output.write_bytes(download.content)
                for seconds, channel in [("0.5", 0), ("1.5", 2)]:
                    sample = subprocess.run([
                        "ffmpeg", "-v", "error", "-nostdin", "-ss", seconds, "-i", str(output),
                        "-frames:v", "1", "-vf", "scale=1:1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
                    ], check=True, capture_output=True, timeout=15).stdout
                    self.assertEqual(len(sample), 3)
                    self.assertGreater(sample[channel], 180)
                caption_pixels = subprocess.run([
                    "ffmpeg", "-v", "error", "-nostdin", "-ss", "0.5", "-i", str(output),
                    "-frames:v", "1", "-vf", "crop=760:100:260:552", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
                ], check=True, capture_output=True, timeout=15).stdout
                lit = sum(min(rgb) > 190 for rgb in zip(caption_pixels[0::3], caption_pixels[1::3], caption_pixels[2::3]))
                self.assertGreater(lit, 100, "Tekst skal vere synleg i den elles raude ramma.")

        self.assertEqual([r.url.path for r in requests], [
            f"/v1beta/models/{gemini.DEFAULT_MODEL}:generateContent",
            f"/v1beta/models/{gemini.RENDER_RESERVE_MODEL}:generateContent",
        ])
        payloads = [json.loads(r.content)["contents"][0]["parts"] for r in requests]
        self.assertEqual(payloads[0], payloads[1])
        self.assertGreater(requests[0].extensions["timeout"]["read"], 60)
        parts = payloads[0]
        self.assertEqual([json.loads(part["text"])["clip_id"] for part in parts[:-1]
                          if "text" in part and "clip_id" in json.loads(part["text"])], clip_ids)
        blobs = [part["inlineData"] for part in parts if "inlineData" in part]
        self.assertEqual(len(blobs), 10)
        for blob in blobs:
            self.assertEqual(blob.get("mimeType", blob.get("mime_type")), "image/jpeg")
            self.assertTrue(base64.urlsafe_b64decode(blob["data"]).startswith(b"\xff\xd8"))

    def test_real_sdk_serializes_upload_analysis_and_cleanup_without_network(self):
        requests = []
        analysis_options = []
        reserved_name = None

        def transport(request):
            nonlocal reserved_name
            requests.append((request.method, request.url.path))
            if request.url.path == "/upload/v1beta/files":
                reserved_name = json.loads(request.content)["file"]["name"]
                return httpx.Response(200, headers={
                    "x-goog-upload-url": "https://generativelanguage.googleapis.com/upload/local-test",
                }, json={})
            if request.url.path == "/upload/local-test":
                self.assertEqual(request.content, b"Local SDK transport fixture")
                return httpx.Response(200, headers={"x-goog-upload-status": "final"}, json={"file": {
                    "name": reserved_name,
                    "uri": "https://generativelanguage.googleapis.com/v1beta/" + reserved_name,
                    "mimeType": "video/mp4", "state": "ACTIVE",
                }})
            if request.url.path.endswith(":generateContent"):
                payload = json.loads(request.content)
                self.assertEqual(payload["generationConfig"]["responseMimeType"], "application/json")
                self.assertIn("responseJsonSchema", payload["generationConfig"])
                self.assertNotIn("temperature", payload["generationConfig"])
                analysis_options.append({
                    "thinking": payload["generationConfig"].get("thinkingConfig"),
                    "timeout": request.extensions.get("timeout"),
                })
                self.assertIn("fileData", payload["contents"][0]["parts"][1])
                return httpx.Response(200, json={"candidates": [{
                    "content": {"role": "model", "parts": [{"text": json.dumps({"segments": [
                        {"clip_id": "clip", "start_seconds": 0, "end_seconds": 1, "order": 1},
                    ]})}]}, "finishReason": "STOP",
                }]})
            if request.method == "DELETE":
                self.assertEqual(request.url.path, "/v1beta/" + reserved_name)
                return httpx.Response(200, json={})
            self.fail("Uventa førespurnad i den lokale SDK-testen.")

        with TemporaryDirectory() as temporary:
            source = Path(temporary) / "input.mp4"
            source.write_bytes(b"Local SDK transport fixture")
            options = gemini._http_options()
            options.httpx_client = httpx.Client(transport=httpx.MockTransport(transport), trust_env=False)
            # SDK-et opprettar også ein ubrukt asynkron klient. Han skal heller
            # ikkje arve proxyar eller andre nettverksinnstillingar frå maskina.
            options.async_client_args = {"trust_env": False}
            # Ein statisk testverdi blir berre brukt av den lokale mock-transporten.
            client = genai.Client(api_key="offline-test-key", vertexai=False, http_options=options)
            with patch("app.gemini._client", return_value=client), patch(
                "httpx.HTTPTransport.handle_request", side_effect=AssertionError("Eksternt HTTP-nettverk er forbode i denne testen."),
            ), patch("app.gemini.INLINE_SOURCE_SECONDS", 0):
                plan = gemini.generate_plan(
                    [{"id": "clip", "path": str(source), "duration": 2, "has_audio": False}],
                    "Vel eitt sekund.", 1, "energetic", lambda *_: None, model=gemini.DEFAULT_MODEL,
                )
            self.assertEqual(plan[0]["duration"], 1)
            self.assertEqual(len(requests), 4)  # Éi upload-økt, éin analyse, éi sletting.
            self.assertEqual(requests[2], ("POST", f"/v1beta/models/{gemini.DEFAULT_MODEL}:generateContent"))
            thinking = analysis_options[0]["thinking"]
            # SDK-versjonar kan bruke proto-namnet eller JSON-namnet i denne nested meldinga.
            self.assertEqual(thinking.get("thinkingLevel", thinking.get("thinking_level", "")).lower(), "low")
            self.assertEqual(analysis_options[0]["timeout"]["read"], 180)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg må vere installert")
    def test_short_video_with_audio_is_sent_in_the_analysis_request(self):
        requests = []

        def transport(request):
            requests.append(request)
            return httpx.Response(200, json={"candidates": [{
                "content": {"role": "model", "parts": [{"text": json.dumps({"segments": [
                    {"clip_id": "clip", "start_seconds": 0, "end_seconds": 1, "order": 1},
                ]})}]}, "finishReason": "STOP",
            }]})

        with TemporaryDirectory() as temporary:
            source = Path(temporary) / "original.mp4"
            subprocess.run([
                "ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=24:d=2",
                "-f", "lavfi", "-i", "sine=d=2", "-c:v", "libx264", "-threads", "1",
                "-c:a", "aac", str(source),
            ], check=True, timeout=30)
            options = gemini._http_options()
            options.httpx_client = httpx.Client(transport=httpx.MockTransport(transport), trust_env=False)
            options.async_client_args = {"trust_env": False}
            client = genai.Client(api_key="offline-test-key", vertexai=False, http_options=options)
            reserved = []
            with patch("app.gemini._client", return_value=client):
                plan = gemini.generate_plan(
                    [{"id": "clip", "path": str(source), "duration": 2, "has_audio": True}],
                    "Vel eitt sekund.", 1, "energetic", lambda *_: None, on_uploaded=reserved.append,
                    model=gemini.DEFAULT_MODEL,
                )
            self.assertFalse(reserved)
            self.assertEqual(plan[0]["path"], str(source.resolve()))
            self.assertEqual(len(requests), 1)
            self.assertTrue(requests[0].url.path.endswith(":generateContent"))
            parts = json.loads(requests[0].content)["contents"][0]["parts"]
            self.assertEqual(json.loads(parts[0]["text"])["clip_id"], "clip")
            self.assertEqual(parts[1]["videoMetadata"]["fps"], 1)
            blob = parts[1]["inlineData"]
            self.assertEqual(blob.get("mimeType", blob.get("mime_type")), "video/mp4")
            encoded = base64.urlsafe_b64decode(blob["data"])
            self.assertLess(len(encoded), source.stat().st_size)
            copy = Path(temporary) / "copy.mp4"
            copy.write_bytes(encoded)
            info = media.probe(copy)
            self.assertTrue(info["has_audio"])
            self.assertEqual((info["width"], info["height"]), (320, 180))
            self.assertAlmostEqual(info["duration"], 2, delta=1 / 6)


if __name__ == "__main__":
    unittest.main()
