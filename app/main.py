"""FastAPI, SQLite og éin bakgrunnsarbeidar for den lokale demonstrasjonen.

Start med: python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Lokal bruk utan innlogging; offentleg lærardemo krev vertsnamn og passord.
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
import time
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

from . import gemini, media, narration
from .hosting import HostingSettings, configure_hosting
from .schemas import EditInput, ProjectInput, ScheduleInput

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)
LOGGER = logging.getLogger(__name__)
MAX_UPLOAD = 200 * 1024 * 1024
MAX_STORAGE = 2 * 1024 * 1024 * 1024
MAX_FILES = 12
MAX_CLIP_SECONDS = 1800
MAX_EXPORT_SECONDS = media.MAX_EXPORT_SECONDS
EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
MIME_TYPES = {"video/mp4", "video/quicktime", "video/x-matroska", "video/webm", "application/octet-stream"}
DEFAULT_SETTINGS = {"duration_seconds": 30, "aspect": "16:9", "fit": "pad", "style": "professional", "subtitles": False, "mode": "basic", "ai_consent": False}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
                CREATE TABLE IF NOT EXISTS gemini_files (
                    file_name TEXT PRIMARY KEY,
                    job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
                    created_at TEXT NOT NULL
                );
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
            if "plan" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN plan TEXT")
            if "fallback_reason" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN fallback_reason TEXT")

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
            job["plan"] = json.loads(job["plan"]) if job["plan"] else None
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
        # Køyr nettverksopprydding i arbeidstråden, aldri i HTTP-oppstarten.
        self.cleanup_pending(deadline=time.monotonic() + gemini.CLEANUP_WAIT_SECONDS)
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

    def cleanup_pending(self, *, deadline=None):
        names = [row["file_name"] for row in self.store.all("SELECT file_name FROM gemini_files")]
        if not names:
            return True
        inaccessible = set()
        remaining = gemini.cleanup_files(
            names, lambda name: self.store.execute("DELETE FROM gemini_files WHERE file_name=?", (name,)),
            on_inaccessible=inaccessible.add, deadline=deadline,
        )
        if remaining:
            LOGGER.warning("Opprydding av %s mellombelse Google-filer står att. Ingen analyse vart starta av oppryddinga.", len(remaining))
        # Historiske 403-referansar som manglar i ei fullført filliste blir
        # bevarte for seinare opprydding, men låser ikkje nye prosjekt.
        return not (set(remaining) - inaccessible)

    @staticmethod
    def log_gemini_failure(job_id, error):
        # Ikkje logg SDK-unntak, HTTP-svar, instruksjonar eller nøkkelverdiar.
        LOGGER.warning("Gemini-jobb %s feila (%s, steg=%s, HTTP=%s)",
                       job_id, error.code, error.operation or "ukjent", error.http_status or "ukjent")

    def process(self, job):
        output_dir = self.store.directory / "outputs" / job["id"]
        output_dir.mkdir(exist_ok=True)
        output = output_dir / "video.mp4"
        try:
            settings = json.loads(job["settings"])
            clips = self.store.source_clips(job["project_id"])
            def progress(percent, stage):
                self.store.execute("UPDATE jobs SET progress=?,stage=? WHERE id=?", (min(99, max(2, int(percent))), stage, job["id"]))

            ai_mode = settings.get("mode", "basic") == "gemini"
            if ai_mode:
                configuration_error = gemini.configuration_error()
                if configuration_error:
                    raise gemini.GeminiError(configuration_error, "configuration")
                if not settings.get("ai_consent"):
                    raise gemini.GeminiError("AI-jobben manglar samtykke til å sende klippa til Google.", "consent")
                requirements = gemini.editorial_requirements(settings["description"])
                if requirements["narration_requested"] and not narration.availability()["available"]:
                    raise media.MediaError("Norsk forteljarstemme er ikkje tilgjengeleg på denne maskina. Installer ei norsk Windows-stemme eller fjern ønsket om tale.")
                def remember_file(name):
                    self.store.execute("INSERT OR IGNORE INTO gemini_files(file_name,job_id,created_at) VALUES(?,?,?)", (name, job["id"], now()))

                def forget_file(name):
                    self.store.execute("DELETE FROM gemini_files WHERE file_name=?", (name,))

                reserve = settings.get("basic_fallback") is True
                started = time.monotonic()
                try:
                    progress(2, "Ryddar eventuelle tidlegare Google-filer")
                    cleanup_deadline = started + gemini.CLEANUP_WAIT_SECONDS if reserve else None
                    if not self.cleanup_pending(deadline=cleanup_deadline):
                        raise gemini.GeminiError("Tidlegare Google-filer kunne ikkje ryddast eller kontrollerast. Kontroller API-tilgang og nettverk før du prøver igjen.", "cleanup")
                    plan = gemini.generate_plan(
                        clips, settings["description"], settings["duration_seconds"], settings["style"], progress,
                        on_uploaded=remember_file, on_deleted=forget_file, model=settings.get("gemini_model"),
                        deadline=started + gemini.RESERVE_WAIT_SECONDS if reserve else None,
                    )
                except gemini.GeminiError as error:
                    if not reserve:
                        raise
                    self.log_gemini_failure(job["id"], error)
                    self.store.execute(
                        "UPDATE jobs SET fallback_reason=?, stage=?, progress=43 WHERE id=?",
                        (error.code, "Fullfører med enkel redigering utan AI", job["id"]),
                    )
                    plan = media.build_plan(clips, settings["duration_seconds"], settings["style"])
            else:
                plan = media.build_plan(clips, settings["duration_seconds"], settings["style"])
            media.validate_plan(plan, clips, settings["duration_seconds"])
            public_plan = [{"clip_id": part["clip_id"], "start_seconds": part["start"],
                            "end_seconds": part["start"] + part["duration"], "order": index + 1,
                            **({"ranking": part["ranking"]} if part.get("ranking") else {}),
                            **({"presentation": part["presentation"]} if part.get("presentation") else {}),
                            **({"voiceover": part["voiceover"]} if part.get("voiceover") else {})}
                           for index, part in enumerate(plan)]
            self.store.execute("UPDATE jobs SET plan=? WHERE id=?", (json.dumps(public_plan), job["id"]))

            def render_progress(percent, stage):
                progress(45 + int(percent * 0.54) if ai_mode else percent, stage)

            result = media.render(plan, output, settings["aspect"], settings["fit"], settings["style"], render_progress)
            size = output.stat().st_size
            if self.store.usage()["storage_bytes"] + size > MAX_STORAGE:
                raise media.MediaError("Lagringsgrensa på 2 GiB er nådd. Slett eit prosjekt og prøv igjen.")
            self.store.execute("""UPDATE jobs SET status='completed',stage='Ferdig',progress=100,finished_at=?,
                               output_duration=?,output_width=?,output_height=?,output_bytes=? WHERE id=?""",
                               (now(), result["duration"], result["width"], result["height"], size, job["id"]))
        except Exception as error:
            shutil.rmtree(output_dir, ignore_errors=True)
            message = str(error) if isinstance(error, media.MediaError) else "Videobehandlinga feila. Kontroller serverloggen og prøv igjen."
            if isinstance(error, gemini.GeminiError):
                self.log_gemini_failure(job["id"], error)
            else:
                LOGGER.exception("Videobehandling feila for jobb %s", job["id"])
            self.store.execute("UPDATE jobs SET status='failed',stage='Behandlinga feila',error=?,finished_at=? WHERE id=?",
                               (message, now(), job["id"]))


def create_app(data_dir: Path | None = None) -> FastAPI:
    hosting = HostingSettings.from_environment()
    directory = Path(data_dir or os.getenv("DATA_DIR", ROOT / "data")).expanduser().resolve()
    store = Store(directory)
    worker = VideoWorker(store)
    mutation_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(application):
        media.encoding_threads()
        worker.start()
        try:
            yield
        finally:
            await asyncio.to_thread(worker.stop)

    application = FastAPI(title="AI-prosjektet – lokal videodemo", lifespan=lifespan)
    application.state.store = store
    application.state.worker = worker
    configure_hosting(application, hosting, MAX_UPLOAD)

    @application.api_route("/healthz", methods=["GET", "HEAD"], include_in_schema=False)
    def health():
        try:
            database_ready = store.one("SELECT 1 AS ready")["ready"] == 1
        except sqlite3.Error:
            LOGGER.exception("Helsesjekken fekk ikkje kontakt med databasen")
            database_ready = False
        ready = database_ready and worker.thread.is_alive() and media.tools_available()
        revision = os.getenv("RENDER_GIT_COMMIT", "") if hosting.on_render else ""
        headers = {"X-Klipp-Revision": revision} if len(revision) == 40 and all(
            char in "0123456789abcdef" for char in revision
        ) else {}
        return JSONResponse({"status": "ok" if ready else "unavailable"},
                            status_code=200 if ready else 503, headers=headers)

    @application.get("/api/config")
    def config():
        pending_cleanup = store.one("""SELECT COUNT(*) AS n FROM gemini_files f
            LEFT JOIN jobs j ON j.id=f.job_id
            WHERE j.id IS NULL OR j.status NOT IN ('queued','processing')""")["n"]
        return {"mode": "basic", "local_demo": not hosting.hostname, "hosting": hosting.public_details(),
                "ai_available": gemini.configured(),
                "gemini_configuration_error": gemini.configuration_error(),
                "narration_available": narration.availability()["available"],
                "gemini_cleanup_warning": (
                    "Opprydding av tidlegare Google-filer står att. Referansane er bevarte og blir prøvde igjen. "
                    "Gamle referansar utan tilgang som manglar i Google-prosjektet si filliste, blokkerer ikkje nye jobbar."
                    if pending_cleanup else None),
                "gemini_model": gemini.model_name(), "analysis_timeout_seconds": gemini.ANALYSIS_TIMEOUT_MS // 1000,
                "gemini_reserve_wait_seconds": gemini.RESERVE_WAIT_SECONDS,
                "transcription_available": False,
                "ffmpeg_available": media.tools_available(), "timezone": "Europe/Oslo",
                "limits": {"max_upload_mb": 200, "max_files": MAX_FILES, "max_clip_seconds": MAX_CLIP_SECONDS,
                           "max_export_seconds": MAX_EXPORT_SECONDS, "max_storage_mb": 2048,
                           "max_ai_files": gemini.MAX_AI_FILES, "max_ai_source_seconds": gemini.MAX_AI_SOURCE_SECONDS}}

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
            if body.mode == "gemini":
                if not body.ai_consent:
                    raise HTTPException(400, "Du må samtykkje til at klippa og instruksjonen blir sende til Google for AI-analyse.")
                if not body.description.strip():
                    raise HTTPException(400, "Skriv kva du ønskjer at Gemini skal lage.")
                if not gemini.configured():
                    raise HTTPException(503, gemini.configuration_error())
                try:
                    requirements = gemini.editorial_requirements(body.description)
                except gemini.GeminiError as error:
                    raise HTTPException(400, str(error)) from None
                if requirements["narration_requested"] and not narration.availability()["available"]:
                    raise HTTPException(400, "Norsk forteljarstemme er ikkje tilgjengeleg på denne maskina. Installer ei norsk Windows-stemme eller fjern ønsket om tale.")
                if len(clips) > gemini.MAX_AI_FILES or sum(clip["duration"] for clip in clips) > gemini.MAX_AI_SOURCE_SECONDS:
                    raise HTTPException(400, "AI-modus støttar høgst ti klipp og 30 minutt samla kjeldemateriale per jobb.")
            try:
                plan = media.build_plan(clips, body.duration_seconds, body.style)
                media.validate_plan(plan, clips, body.duration_seconds)
            except media.MediaError as error:
                raise HTTPException(400, str(error)) from error
            identifier = uuid4().hex
            settings = body.model_dump(exclude={"description"})
            job_settings = {**settings, "description": body.description}
            if body.mode == "gemini":
                job_settings["gemini_model"] = gemini.model_name()
            with store.connect() as db:
                db.execute("UPDATE projects SET description=?,settings=? WHERE id=?", (body.description, json.dumps(settings), project_id))
                db.execute("INSERT INTO jobs(id,project_id,status,stage,settings,created_at) VALUES(?,?,'queued','Ventande i kø',?,?)",
                           (identifier, project_id, json.dumps(job_settings), now()))
            worker.wakeup.set()
            return get_job(identifier)

    @application.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = store.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not job:
            raise HTTPException(404, "Jobben finst ikkje.")
        job["settings"] = json.loads(job["settings"])
        job["plan"] = json.loads(job["plan"]) if job["plan"] else None
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
