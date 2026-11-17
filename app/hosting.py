"""Vertsnamn, passord og HTTP-grenser for lokal bruk og Render."""

import base64
from dataclasses import dataclass
import os
import secrets
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware


@dataclass(frozen=True)
class HostingSettings:
    hostname: str
    password: str
    on_render: bool
    ephemeral_storage: bool

    @classmethod
    def from_environment(cls):
        hostname = (os.getenv("PUBLIC_HOST", "").strip()
                    or os.getenv("RENDER_EXTERNAL_HOSTNAME", "").strip())
        password = os.getenv("DEMO_PASSWORD", "")
        on_render = os.getenv("RENDER", "").lower() == "true" or bool(os.getenv("RENDER_EXTERNAL_HOSTNAME"))
        if on_render and not hostname:
            raise RuntimeError("Render manglar RENDER_EXTERNAL_HOSTNAME. Set PUBLIC_HOST til tenesta sitt vertsnamn.")
        if hostname and (any(c in hostname for c in "/:*?#@") or any(c.isspace() for c in hostname)):
            raise RuntimeError("PUBLIC_HOST må vere eit eksakt vertsnamn utan https://, port eller sti.")
        if (hostname or password) and len(password) < 16:
            raise RuntimeError("DEMO_PASSWORD må ha minst 16 teikn når passordvern eller offentleg hosting er aktivert.")
        persistent = os.getenv("PERSISTENT_STORAGE", "").lower() == "true"
        return cls(hostname, password, on_render, on_render and not persistent)

    def public_details(self):
        return {
            "hosted": bool(self.hostname),
            "provider": "render" if self.on_render else "other" if self.hostname else "local",
            "password_protected": bool(self.hostname or self.password),
            "ephemeral_storage": self.ephemeral_storage,
        }


class BodyLimitMiddleware:
    """Avgrens også chunked-opplasting før multipart-parseren skriv heile fila."""

    def __init__(self, app, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            content_length = int(headers.get(b"content-length", b"0"))
            if content_length < 0:
                raise ValueError
        except ValueError:
            return await JSONResponse({"detail": "Ugyldig førespurnadsstorleik."}, 400)(scope, receive, send)
        if content_length > self.max_bytes:
            return await JSONResponse({"detail": "Fila er for stor. Grensa er 200 MiB per fil."}, 413)(scope, receive, send)
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise HTTPException(413, "Fila er for stor. Grensa er 200 MiB per fil.")
            return message

        await self.app(scope, limited_receive, send)


def configure_hosting(application, settings: HostingSettings, max_upload_bytes: int):
    application.add_middleware(BodyLimitMiddleware, max_bytes=max_upload_bytes + 1024 * 1024)
    hosts = ["localhost", "127.0.0.1", "[::1]", "testserver"]
    if settings.hostname:
        hosts.append(settings.hostname)
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    @application.middleware("http")
    async def protect_requests(request: Request, call_next):
        health_check = request.url.path == "/healthz" and request.method in ("GET", "HEAD")
        if (settings.hostname or settings.password) and not health_check:
            authenticated = False
            try:
                scheme, encoded = request.headers.get("authorization", "").split(" ", 1)
                user, password = base64.b64decode(encoded, validate=True).decode("utf-8").split(":", 1)
                authenticated = (scheme.lower() == "basic" and
                                 secrets.compare_digest(user.encode(), b"laerar") and
                                 secrets.compare_digest(password.encode(), settings.password.encode()))
            except (ValueError, UnicodeError):
                pass
            if not authenticated:
                return Response(status_code=401, headers={
                    "WWW-Authenticate": 'Basic realm="Laerardemo", charset="UTF-8"',
                    "Cache-Control": "no-store",
                })
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin:
            parsed = urlsplit(origin)
            if parsed.scheme not in ("http", "https") or parsed.netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Førespurnaden må kome frå denne nettsida."}, 403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        if request.url.path.startswith("/api/") or health_check:
            response.headers["Cache-Control"] = "no-store"
        return response
