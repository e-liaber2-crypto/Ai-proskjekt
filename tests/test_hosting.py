import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import create_app


class HostingTests(unittest.TestCase):
    def test_public_demo_requires_password_and_preserves_origin_checks(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {
            "PUBLIC_HOST": "demo.example.org", "DEMO_PASSWORD": "test-password-for-demo"
        }):
            client = TestClient(create_app(Path(directory)), base_url="https://demo.example.org")
            for path in ("/", "/app.js", "/api/projects", "/docs"):
                self.assertEqual(client.get(path).status_code, 401)
            self.assertEqual(client.get("/healthz").status_code, 200)
            self.assertEqual(client.get("/", auth=("laerar", "wrong")).status_code, 401)
            self.assertEqual(client.get("/", headers={"Authorization": "Basic !!!"}).status_code, 401)
            client.auth = ("laerar", "test-password-for-demo")
            self.assertEqual(client.get("/").status_code, 200)
            self.assertEqual(client.get("/api/projects").status_code, 200)
            self.assertEqual(client.post("/api/projects", json={"name": "Demo"}, headers={"Origin": "https://other.example.org"}).status_code, 403)
            self.assertEqual(client.post("/api/projects", json={"name": "Demo"}, headers={"Origin": "https://demo.example.org"}).status_code, 201)
            self.assertEqual(client.get("/", headers={"Host": "other.example.org"}).status_code, 400)

    def test_public_host_without_strong_password_fails_closed(self):
        with patch.dict("os.environ", {"PUBLIC_HOST": "demo.example.org", "DEMO_PASSWORD": ""}):
            with self.assertRaises(RuntimeError):
                create_app()
