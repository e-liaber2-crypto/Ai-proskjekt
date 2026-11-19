"""Render-oppsett, tilgangsvern og helsesjekk utan eksterne tenester."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.hosting import HostingSettings
from app.main import create_app


class HostingTests(unittest.TestCase):
    def test_public_demo_requires_password_and_preserves_origin_checks(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {
            "PUBLIC_HOST": "demo.example.org", "DEMO_PASSWORD": "test-password-for-demo"
        }), TestClient(create_app(Path(directory)), base_url="https://demo.example.org") as client:
            for path in ("/", "/app.js", "/api/projects", "/docs"):
                self.assertEqual(client.get(path).status_code, 401)
            self.assertEqual(client.get("/healthz").status_code, 200)
            self.assertEqual(client.head("/healthz").status_code, 200)
            self.assertEqual(client.head("/healthz").content, b"")
            self.assertEqual(client.get("/", auth=("laerar", "wrong")).status_code, 401)
            self.assertEqual(client.get("/", headers={"Authorization": "Basic !!!"}).status_code, 401)
            client.auth = ("laerar", "test-password-for-demo")
            self.assertEqual(client.get("/").status_code, 200)
            self.assertEqual(client.get("/api/projects").status_code, 200)
            self.assertEqual(client.post("/api/projects", json={"name": "Demo"}, headers={"Origin": "https://other.example.org"}).status_code, 403)
            self.assertEqual(client.post("/api/projects", json={"name": "Demo"}, headers={"Origin": "https://demo.example.org"}).status_code, 201)
            self.assertEqual(client.get("/", headers={"Host": "other.example.org"}).status_code, 400)
            self.assertEqual(client.get("/healthz", headers={"Host": "other.example.org"}).status_code, 400)
            for path in ("/.env", "/data/app.db", "/app/main.py"):
                self.assertEqual(client.get(path).status_code, 404)

    def test_public_host_without_strong_password_fails_closed(self):
        with patch.dict("os.environ", {"PUBLIC_HOST": "demo.example.org", "DEMO_PASSWORD": ""}):
            with self.assertRaises(RuntimeError):
                create_app()

    def test_render_uses_automatic_hostname_even_with_empty_public_host(self):
        with patch.dict("os.environ", {
            "PUBLIC_HOST": "", "RENDER": "true", "RENDER_EXTERNAL_HOSTNAME": "demo.onrender.com",
            "DEMO_PASSWORD": "test-password-for-demo", "PERSISTENT_STORAGE": "false",
            "RENDER_GIT_COMMIT": "a" * 40,
        }), tempfile.TemporaryDirectory() as directory, TestClient(
            create_app(Path(directory)), base_url="https://demo.onrender.com"
        ) as client:
            response = client.get("/healthz")
            self.assertEqual(response.json(), {"status": "ok"})
            self.assertEqual(response.headers["X-Klipp-Revision"], "a" * 40)
            self.assertEqual(client.get("/api/config").status_code, 401)
            config = client.get("/api/config", auth=("laerar", "test-password-for-demo")).json()
            self.assertFalse(config["local_demo"])
            self.assertEqual(config["hosting"], {
                "hosted": True, "provider": "render", "password_protected": True, "ephemeral_storage": True,
            })
            self.assertNotIn("test-password-for-demo", str(config))

    def test_render_without_hostname_cannot_start_unprotected(self):
        with patch.dict("os.environ", {"RENDER": "true", "PUBLIC_HOST": "", "RENDER_EXTERNAL_HOSTNAME": ""}):
            with self.assertRaises(RuntimeError):
                HostingSettings.from_environment()

    def test_health_fails_when_video_engine_or_worker_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory, TestClient(create_app(Path(directory))) as client:
            self.assertEqual(client.get("/healthz").status_code, 200)
            with patch("app.main.media.tools_available", return_value=False):
                response = client.get("/healthz")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {"status": "unavailable"})
            with patch.object(client.app.state.worker.thread, "is_alive", return_value=False):
                self.assertEqual(client.get("/healthz").status_code, 503)

    def test_health_does_not_hide_database_failure(self):
        with tempfile.TemporaryDirectory() as directory, TestClient(create_app(Path(directory))) as client:
            import sqlite3
            with patch.object(client.app.state.store, "one", side_effect=sqlite3.OperationalError("offline")):
                with self.assertLogs("app.main", level="ERROR"):
                    self.assertEqual(client.get("/healthz").status_code, 503)

    def test_optional_persistent_storage_requires_explicit_setting(self):
        with patch.dict("os.environ", {
            "RENDER_EXTERNAL_HOSTNAME": "demo.onrender.com", "PUBLIC_HOST": "",
            "DEMO_PASSWORD": "test-password-for-demo", "PERSISTENT_STORAGE": "true",
        }):
            self.assertFalse(HostingSettings.from_environment().ephemeral_storage)

    def test_weak_local_password_and_invalid_hosts_are_rejected(self):
        for hostname, password in (("", "short"), ("https://demo.example.org", "a" * 20), ("bad host", "a" * 20)):
            with self.subTest(hostname=hostname), patch.dict("os.environ", {
                "PUBLIC_HOST": hostname, "DEMO_PASSWORD": password, "RENDER": "", "RENDER_EXTERNAL_HOSTNAME": "",
            }):
                with self.assertRaises(RuntimeError):
                    HostingSettings.from_environment()
