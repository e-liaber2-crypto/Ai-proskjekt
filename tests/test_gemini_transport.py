"""Sjekk det verkelege Google-SDK-et med ein heilt lokal HTTP-transport."""

import base64
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from google import genai
import httpx

from app import gemini, media


class GeminiTransportTests(unittest.TestCase):
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
