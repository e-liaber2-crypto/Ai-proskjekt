"""FastAPI, SQLite og éin bakgrunnsarbeidar for den lokale demonstrasjonen.

Start med: python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Ingen innlogging: bind berre til localhost, og ikkje set denne ut offentleg.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import threading
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import media

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)
LOGGER = logging.getLogger(__name__)
MAX_UPLOAD = 200 * 1024 * 1024
MAX_STORAGE = 2 * 1024 * 1024 * 1024
MAX_FILES = 12
MAX_CLIP_SECONDS = 1800
MAX_EXPORT_SECONDS = 600
ACTIVE = ("queued", "processing")
EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
MIME_TYPES = {"video/mp4", "video/quicktime", "video/x-matroska", "video/webm", "application/octet-stream"}
DEFAULT_SETTINGS = {"duration_seconds": 30, "aspect": "16:9", "fit": "pad", "style": "professional", "subtitles": False}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("Prosjektnamnet må innehalde lesbar tekst.")
        return value


class EditInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(default="", max_length=4000)
    duration_seconds: int = Field(ge=1, le=MAX_EXPORT_SECONDS, strict=True)
    aspect: Literal["16:9", "9:16", "1:1"] = "16:9"
    fit: Literal["pad", "crop"] = "pad"
    style: Literal["calm", "energetic", "professional"] = "professional"
    subtitles: bool = False


class ScheduleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    channel: Literal["YouTube", "Instagram", "TikTok"]
    scheduled_at: datetime
    timezone: str = Field(default="Europe/Oslo", max_length=80)
    approved: bool = False


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


class Store:
    """Korte SQLite-tilkoplingar kan brukast frå både API-et og arbeidstråden."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "app.db"
        directory.mkdir(parents=True, exist_ok=True)
        for name in ("uploads", "outputs", "temporary"):
            (directory / name).mkdir(exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '', settings TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS clips (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    original_name TEXT NOT NULL, filename TEXT NOT NULL, duration REAL NOT NULL,
                    width INTEGER NOT NULL, height INTEGER NOT NULL, has_audio INTEGER NOT NULL,
                    size_bytes INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    status TEXT NOT NULL, stage TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
                    settings TEXT NOT NULL, created_at TEXT NOT NULL, finished_at TEXT,
                    error TEXT, output_duration REAL, output_width INTEGER, output_height INTEGER,
                    output_bytes INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS schedules (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    channel TEXT NOT NULL, scheduled_at TEXT NOT NULL, timezone TEXT NOT NULL,
                    created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'planned_local',
                    UNIQUE(job_id, channel, scheduled_at)
                );
            """)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def one(self, sql, values=()):
        with self.connect() as db:
            row = db.execute(sql, values).fetchone()
            return dict(row) if row else None

    def all(self, sql, values=()):
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, values).fetchall()]

    def execute(self, sql, values=()):
        with self.connect() as db:
            db.execute(sql, values)

    def require_project(self, project_id):
        project = self.one("SELECT * FROM projects WHERE id=?", (project_id,))
        if not project:
            raise HTTPException(404, "Prosjektet finst ikkje.")
        return project

    def require_idle(self, project_id):
        if self.one("SELECT id FROM jobs WHERE project_id=? AND status IN ('queued','processing')", (project_id,)):
            raise HTTPException(409, "Vent til behandlinga er ferdig før du endrar prosjektet.")

    def source_clips(self, project_id):
        clips = self.all("SELECT * FROM clips WHERE project_id=? ORDER BY created_at, id", (project_id,))
        for clip in clips:
            clip["path"] = str(self.directory / "uploads" / clip["filename"])
            clip["has_audio"] = bool(clip["has_audio"])
        return clips

    def usage(self):
        source = self.one("SELECT COALESCE(SUM(size_bytes),0) AS n FROM clips")["n"]
        output = self.one("SELECT COALESCE(SUM(output_bytes),0) AS n, COALESCE(SUM(output_duration),0) AS seconds FROM jobs WHERE status='completed'")
        return {"source_bytes": source, "output_bytes": output["n"], "storage_bytes": source + output["n"],
                "rendered_seconds": output["seconds"], "storage_limit_bytes": MAX_STORAGE,
                "max_export_seconds": MAX_EXPORT_SECONDS, "plan": "local_demo"}

    def committed_storage(self):
        """Les brukt og reservert plass i det same databasesnapshotet."""
        with self.connect() as db:
            db.execute("BEGIN")
            source = db.execute("SELECT COALESCE(SUM(size_bytes),0) FROM clips").fetchone()[0]
            jobs = db.execute("SELECT status,settings,output_bytes FROM jobs WHERE status IN ('completed','queued','processing')").fetchall()
            return source + sum(row["output_bytes"] if row["status"] == "completed" else
                                int(json.loads(row["settings"])["duration_seconds"] * 420_000 + 5 * 1024 * 1024)
                                for row in jobs)

    def project(self, project_id, detail=True):
        result = self.require_project(project_id)
        result["settings"] = json.loads(result["settings"])
        clips = self.source_clips(project_id)
        jobs = self.all("SELECT * FROM jobs WHERE project_id=? ORDER BY created_at DESC", (project_id,))
        for job in jobs:
            job["settings"] = json.loads(job["settings"])
        result.update(source_duration=sum(c["duration"] for c in clips), clip_count=len(clips),
                      storage_bytes=sum(c["size_bytes"] for c in clips) + sum(j["output_bytes"] for j in jobs),
                      latest_job=jobs[0] if jobs else None)
        if detail:
            result["clips"] = [{k: v for k, v in clip.items() if k not in ("path", "filename")} for clip in clips]
            result["jobs"] = jobs
        return result


class VideoWorker:
    """Éin jobb om gongen. Køen ligg i databasen, ikkje berre i minnet."""

    def __init__(self, store):
        self.store = store
        self.stopping = threading.Event()
        self.wakeup = threading.Event()
        self.thread = threading.Thread(target=self.run, name="video-worker", daemon=True)

    def start(self):
        self.store.execute("UPDATE jobs SET status='failed', stage='Avbroten', error=?, finished_at=? WHERE status='processing'",
                           ("Behandlinga vart avbroten av ein omstart. Start ein ny eksport.", now()))
        # Mellombels opplastingsdata har aldri offentlege lenkjer eller DB-referansar.
        for path in (self.store.directory / "temporary").iterdir():
            if path.is_file():
                path.unlink(missing_ok=True)
        # Ein hard stopp kan ha hindra media.render sin finally-opprydding.
        for job in self.store.all("SELECT id FROM jobs WHERE status='failed'"):
            shutil.rmtree(self.store.directory / "outputs" / job["id"], ignore_errors=True)
        self.thread.start()

    def stop(self):
        self.stopping.set()
        self.wakeup.set()
        self.thread.join()  # Pågåande FFmpeg-steg har eigen tidsgrense.

    def run(self):
        while not self.stopping.is_set():
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
                if row:
                    db.execute("UPDATE jobs SET status='processing', stage='Validerer klippeplan', progress=2 WHERE id=?", (row["id"],))
            if row:
                self.process(dict(row))
            else:
                self.wakeup.wait(1)
                self.wakeup.clear()

    def process(self, job):
        output_dir = self.store.directory / "outputs" / job["id"]
        output_dir.mkdir(exist_ok=True)
        output = output_dir / "video.mp4"
        try:
            settings = json.loads(job["settings"])
            clips = self.store.source_clips(job["project_id"])
            plan = media.build_plan(clips, settings["duration_seconds"], settings["style"])
            media.validate_plan(plan, clips, settings["duration_seconds"])

            def progress(percent, stage):
                self.store.execute("UPDATE jobs SET progress=?,stage=? WHERE id=?", (min(99, max(2, int(percent))), stage, job["id"]))

            result = media.render(plan, output, settings["aspect"], settings["fit"], settings["style"], progress)
            size = output.stat().st_size
            if self.store.usage()["storage_bytes"] + size > MAX_STORAGE:
                raise media.MediaError("Lagringsgrensa på 2 GiB er nådd. Slett eit prosjekt og prøv igjen.")
            self.store.execute("""UPDATE jobs SET status='completed',stage='Ferdig',progress=100,finished_at=?,
                               output_duration=?,output_width=?,output_height=?,output_bytes=? WHERE id=?""",
                               (now(), result["duration"], result["width"], result["height"], size, job["id"]))
        except Exception as error:
            shutil.rmtree(output_dir, ignore_errors=True)
            message = str(error) if isinstance(error, media.MediaError) else "Videobehandlinga feila. Kontroller serverloggen og prøv igjen."
            LOGGER.exception("Videobehandling feila for jobb %s", job["id"])
            self.store.execute("UPDATE jobs SET status='failed',stage='Behandlinga feila',error=?,finished_at=? WHERE id=?",
                               (message, now(), job["id"]))


def create_app(data_dir: Path | None = None) -> FastAPI:
    directory = Path(data_dir or os.getenv("DATA_DIR", ROOT / "data")).expanduser().resolve()
    store = Store(directory)
    worker = VideoWorker(store)
    mutation_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(application):
        worker.start()
        yield
        await asyncio.to_thread(worker.stop)

    application = FastAPI(title="AI-prosjektet – lokal videodemo", lifespan=lifespan)
    application.state.store = store
    application.state.worker = worker
    application.add_middleware(BodyLimitMiddleware, max_bytes=MAX_UPLOAD + 1024 * 1024)
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])

    @application.middleware("http")
    async def local_only(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin:
            parsed = urlsplit(origin)
            if parsed.scheme not in ("http", "https") or parsed.netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Førespurnaden må kome frå den lokale nettsida."}, 403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.get("/api/config")
    def config():
        return {"mode": "basic", "local_demo": True, "ai_available": False, "transcription_available": False,
                "ffmpeg_available": media.tools_available(), "timezone": "Europe/Oslo",
                "limits": {"max_upload_mb": 200, "max_files": MAX_FILES, "max_clip_seconds": MAX_CLIP_SECONDS,
                           "max_export_seconds": MAX_EXPORT_SECONDS, "max_storage_mb": 2048}}

    @application.get("/api/projects")
    def projects():
        return [store.project(row["id"], detail=False) for row in store.all("SELECT id FROM projects ORDER BY created_at DESC")]

    @application.post("/api/projects", status_code=201)
    async def create_project(body: ProjectInput):
        async with mutation_lock:
            if store.one("SELECT COUNT(*) AS n FROM projects")["n"] >= 100:
                raise HTTPException(409, "Grensa er 100 prosjekt i denne demonstrasjonen. Slett eit prosjekt først.")
            identifier = uuid4().hex
            store.execute("INSERT INTO projects(id,name,created_at,settings) VALUES(?,?,?,?)",
                          (identifier, body.name, now(), json.dumps(DEFAULT_SETTINGS)))
            return store.project(identifier)

    @application.get("/api/projects/{project_id}")
    def project(project_id: str):
        return store.project(project_id)

    @application.post("/api/projects/{project_id}/clips", status_code=201)
    async def upload(project_id: str, file: UploadFile = File(...)):
        temporary = directory / "temporary" / uuid4().hex
        destination = None
        try:
            async with mutation_lock:
                store.require_project(project_id)
                store.require_idle(project_id)
                if len(store.source_clips(project_id)) >= MAX_FILES:
                    raise HTTPException(409, "Du kan laste opp maksimalt 12 klipp per prosjekt.")
                original_name = (file.filename or "video").replace("\\", "/").rsplit("/", 1)[-1]
                original_name = "".join(char for char in original_name if ord(char) >= 32)[:160]
                extension = Path(original_name).suffix.lower()
                if extension not in EXTENSIONS or (file.content_type and file.content_type not in MIME_TYPES):
                    raise HTTPException(415, "Vel ei MP4-, MOV-, MKV- eller WebM-videofil.")
                if not media.tools_available():
                    raise HTTPException(503, "FFmpeg eller ffprobe manglar. Sjå installasjonen i README.")
                used = store.committed_storage()
                size = 0
                with temporary.open("xb") as stream:
                    while chunk := await file.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_UPLOAD:
                            raise HTTPException(413, "Grensa er 200 MiB per fil.")
                        if used + size > MAX_STORAGE:
                            raise HTTPException(413, "Lagringsgrensa på 2 GiB er nådd. Slett eit prosjekt først.")
                        stream.write(chunk)
                try:
                    info = await asyncio.to_thread(media.probe, temporary)
                except media.MediaError as error:
                    raise HTTPException(400, str(error)) from error
                if info["duration"] > MAX_CLIP_SECONDS:
                    raise HTTPException(400, "Kvart kjeldeklipp kan vere maksimalt 30 minutt langt.")
                identifier = uuid4().hex
                filename = identifier + extension
                destination = directory / "uploads" / filename
                temporary.replace(destination)
                try:
                    store.execute("""INSERT INTO clips(id,project_id,original_name,filename,duration,width,height,has_audio,size_bytes,created_at)
                                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                                  (identifier, project_id, original_name, filename, info["duration"], info["width"], info["height"], int(info["has_audio"]), size, now()))
                except Exception:
                    destination.unlink(missing_ok=True)
                    raise
                return next(c for c in store.project(project_id)["clips"] if c["id"] == identifier)
        finally:
            temporary.unlink(missing_ok=True)
            await file.close()

    @application.delete("/api/projects/{project_id}/clips/{clip_id}")
    async def delete_clip(project_id: str, clip_id: str):
        async with mutation_lock:
            store.require_project(project_id)
            store.require_idle(project_id)
            clip = store.one("SELECT filename FROM clips WHERE id=? AND project_id=?", (clip_id, project_id))
            if not clip:
                raise HTTPException(404, "Klippet finst ikkje i dette prosjektet.")
            store.execute("DELETE FROM clips WHERE id=?", (clip_id,))
            (directory / "uploads" / clip["filename"]).unlink(missing_ok=True)
            return {"ok": True}

    @application.delete("/api/projects/{project_id}")
    async def delete_project(project_id: str):
        async with mutation_lock:
            store.require_project(project_id)
            store.require_idle(project_id)
            clips = store.source_clips(project_id)
            jobs = store.all("SELECT id FROM jobs WHERE project_id=?", (project_id,))
            store.execute("DELETE FROM projects WHERE id=?", (project_id,))
            for clip in clips:
                Path(clip["path"]).unlink(missing_ok=True)
            for job in jobs:
                shutil.rmtree(directory / "outputs" / job["id"], ignore_errors=True)
            return {"ok": True}

    @application.post("/api/projects/{project_id}/jobs", status_code=202)
    async def start_job(project_id: str, body: EditInput):
        async with mutation_lock:
            store.require_project(project_id)
            store.require_idle(project_id)
            if body.subtitles:
                raise HTTPException(400, "Teksting er ikkje tilgjengeleg. Transkripsjon er ikkje integrert enno.")
            if not media.tools_available():
                raise HTTPException(503, "FFmpeg eller ffprobe manglar. Sjå installasjonen i README.")
            if store.one("SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued','processing')")["n"] >= 4:
                raise HTTPException(409, "Køen er full (maksimalt fire jobbar). Prøv igjen når ein jobb er ferdig.")
            # Set av plass til ein eksport på opptil 10 minutt ved 3 Mbit/s.
            reservation = int(body.duration_seconds * 420_000 + 5 * 1024 * 1024)
            if store.committed_storage() + reservation > MAX_STORAGE:
                raise HTTPException(413, "Det er ikkje nok ledig lagringsplass til eksporten. Slett eit prosjekt først.")
            clips = store.source_clips(project_id)
            try:
                plan = media.build_plan(clips, body.duration_seconds, body.style)
                media.validate_plan(plan, clips, body.duration_seconds)
            except media.MediaError as error:
                raise HTTPException(400, str(error)) from error
            identifier = uuid4().hex
            settings = body.model_dump(exclude={"description"})
            with store.connect() as db:
                db.execute("UPDATE projects SET description=?,settings=? WHERE id=?", (body.description, json.dumps(settings), project_id))
                db.execute("INSERT INTO jobs(id,project_id,status,stage,settings,created_at) VALUES(?,?,'queued','Ventande i kø',?,?)",
                           (identifier, project_id, json.dumps(settings), now()))
            worker.wakeup.set()
            return get_job(identifier)

    @application.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = store.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not job:
            raise HTTPException(404, "Jobben finst ikkje.")
        job["settings"] = json.loads(job["settings"])
        return job

    def result_file(job_id: str, download: bool):
        job = get_job(job_id)
        if job["status"] != "completed":
            raise HTTPException(409, "Videoen er ikkje ferdig enno.")
        path = directory / "outputs" / job["id"] / "video.mp4"
        if not path.is_file():
            raise HTTPException(404, "Videofila manglar. Start ein ny eksport.")
        return FileResponse(path, media_type="video/mp4", filename=f"video-{job['id'][:8]}.mp4",
                            content_disposition_type="attachment" if download else "inline")

    @application.get("/api/jobs/{job_id}/video")
    def video(job_id: str):
        return result_file(job_id, False)

    @application.get("/api/jobs/{job_id}/download")
    def download(job_id: str):
        return result_file(job_id, True)

    @application.get("/api/usage")
    def usage():
        return store.usage()

    @application.get("/api/schedules")
    def schedules():
        return store.all("""SELECT schedules.*,jobs.project_id,projects.name AS project_name FROM schedules
                         JOIN jobs ON jobs.id=schedules.job_id JOIN projects ON projects.id=jobs.project_id
                         ORDER BY scheduled_at""")

    @application.post("/api/schedules", status_code=201)
    async def create_schedule(body: ScheduleInput):
        async with mutation_lock:
            job = get_job(body.job_id)
            if job["status"] != "completed":
                raise HTTPException(409, "Videoen må vere ferdig før du kan lage ein publiseringsplan.")
            if not body.approved:
                raise HTTPException(400, "Du må godkjenne den ferdige videoen, kanalen og tidspunktet.")
            if body.scheduled_at.tzinfo is None:
                raise HTTPException(400, "Tidspunktet må innehalde tidssone eller UTC-offset.")
            try:
                ZoneInfo(body.timezone)
            except (ZoneInfoNotFoundError, ValueError):
                raise HTTPException(400, "Ukjend tidssone. Vel ei gyldig IANA-tidssone.")
            scheduled_at = body.scheduled_at.astimezone(timezone.utc).isoformat()
            if body.scheduled_at <= datetime.now(timezone.utc):
                raise HTTPException(400, "Vel eit tidspunkt i framtida.")
            existing = store.one("SELECT id FROM schedules WHERE job_id=? AND channel=? AND scheduled_at=?",
                                 (body.job_id, body.channel, scheduled_at))
            if existing:
                return next(s for s in schedules() if s["id"] == existing["id"])
            identifier = uuid4().hex
            store.execute("INSERT INTO schedules(id,job_id,channel,scheduled_at,timezone,created_at) VALUES(?,?,?,?,?,?)",
                          (identifier, body.job_id, body.channel, scheduled_at, body.timezone, now()))
            return next(s for s in schedules() if s["id"] == identifier)

    @application.delete("/api/schedules/{schedule_id}")
    async def delete_schedule(schedule_id: str):
        async with mutation_lock:
            if not store.one("SELECT id FROM schedules WHERE id=?", (schedule_id,)):
                raise HTTPException(404, "Planen finst ikkje.")
            store.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
            return {"ok": True}

    # Berre desse tre filene er offentlege. data/, .env og Python-kode blir aldri servert.
    @application.get("/")
    def home():
        return FileResponse(ROOT / "index.html", media_type="text/html")

    @application.get("/styles.css")
    def stylesheet():
        return FileResponse(ROOT / "styles.css", media_type="text/css")

    @application.get("/app.js")
    def javascript():
        return FileResponse(ROOT / "app.js", media_type="text/javascript")

    @application.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return Response(status_code=204)

    return application


app = create_app()
