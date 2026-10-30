"""Sjekk det verkelege Google-SDK-et med ein heilt lokal HTTP-transport."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from google import genai
import httpx

from app import gemini


class GeminiTransportTests(unittest.TestCase):
    def test_real_sdk_serializes_upload_analysis_and_cleanup_without_network(self):
        requests = []
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
            options.httpx_client = httpx.Client(transport=httpx.MockTransport(transport))
            # Ein statisk testverdi blir berre brukt av den lokale mock-transporten.
            client = genai.Client(api_key="offline-test-key", vertexai=False, http_options=options)
            with patch("app.gemini._client", return_value=client), patch(
                "socket.socket.connect", side_effect=AssertionError("Nettverk er forbode i denne testen."),
            ):
                plan = gemini.generate_plan(
                    [{"id": "clip", "path": str(source), "duration": 2, "has_audio": False}],
                    "Vel eitt sekund.", 1, "energetic", lambda *_: None, model=gemini.DEFAULT_MODEL,
                )
            self.assertEqual(plan[0]["duration"], 1)
            self.assertEqual(len(requests), 4)  # Éi upload-økt, éin analyse, éi sletting.
            self.assertEqual(requests[2], ("POST", f"/v1beta/models/{gemini.DEFAULT_MODEL}:generateContent"))


if __name__ == "__main__":
    unittest.main()
