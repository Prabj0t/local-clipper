from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .config import Config
from .domain import JobRequest, ProjectCreate, ProjectEdit
from .queue import Coordinator
from .storage import Conflict, InstanceLock, Store

WEB = Path(__file__).parent / "web"


def create_app(config: Config | None = None) -> FastAPI:
    config = config or Config.from_env()
    store = Store(config.data_dir)
    token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        lock = InstanceLock(config.data_dir)
        lock.acquire()
        coordinator = None
        try:
            store.initialize()
            if config.worker_enabled:
                coordinator = Coordinator(config, store)
                coordinator.start()
            app.state.coordinator = coordinator
            yield
        finally:
            try:
                if coordinator:
                    coordinator.stop()
            finally:
                lock.release()

    app = FastAPI(
        title="Local Clipper", version=__version__, lifespan=lifespan,
        docs_url=None, redoc_url=None, openapi_url="/api/openapi.json",
    )
    app.state.store = store
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @app.middleware("http")
    async def browser_boundary(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin:
            try:
                source, target = urlparse(origin), urlparse(str(request.url))
                same = (source.scheme, source.hostname, source.port or 80) == (
                    target.scheme, target.hostname, target.port or 80
                )
            except ValueError:
                same = False
            if not same:
                return JSONResponse({"detail": "Cross-origin requests are not allowed."}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            supplied = request.headers.get("x-clipper-token", "")
            if not secrets.compare_digest(supplied, token):
                return JSONResponse({"detail": "Reload the page before making changes."}, status_code=403)
            try:
                length = int(request.headers.get("content-length", "-1"))
            except ValueError:
                length = -1
            if length < 0:
                return JSONResponse({"detail": "A valid Content-Length is required."}, status_code=411)
            if length > 16 * 1024 * 1024:
                return JSONResponse({"detail": "Request is too large."}, status_code=413)
        response = await call_next(request)
        response.headers.update({
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; media-src 'self'; connect-src 'self'; "
                "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
            ),
        })
        return response

    @app.exception_handler(KeyError)
    async def not_found(_request, _error):
        return JSONResponse({"detail": "Project or job not found."}, status_code=404)

    @app.exception_handler(Conflict)
    async def conflict(_request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(ValueError)
    async def bad_value(_request, error):
        return JSONResponse({"detail": str(error)}, status_code=422)

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")

    @app.get("/api/bootstrap")
    def bootstrap():
        return {"version": __version__, "token": token, "capabilities": config.capabilities()}

    @app.get("/api/health")
    def health():
        coordinator = app.state.coordinator
        healthy = not config.worker_enabled or coordinator is not None and coordinator.thread.is_alive()
        return JSONResponse(
            {"status": "ok" if healthy else "degraded", "version": __version__,
             "worker_enabled": config.worker_enabled},
            status_code=200 if healthy else 503,
        )

    @app.get("/api/projects")
    def projects():
        return store.projects()

    @app.post("/api/projects", status_code=201)
    def create(request: ProjectCreate):
        return store.create(request)

    @app.get("/api/projects/{project_id}")
    def project(project_id: str):
        return {**store.project(project_id), "jobs": store.jobs(project_id)}

    @app.put("/api/projects/{project_id}")
    def edit(project_id: str, request: ProjectEdit):
        return store.edit(project_id, request)

    @app.delete("/api/projects/{project_id}")
    def remove(project_id: str, revision: int = Query(ge=0)):
        store.remove(project_id, revision)
        return {"removed": True, "files_preserved": True}

    @app.post("/api/projects/{project_id}/jobs", status_code=202)
    def enqueue(project_id: str, request: JobRequest):
        return store.enqueue(project_id, request)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        return store.cancel(job_id)

    @app.get("/api/projects/{project_id}/source")
    def source(project_id: str):
        value = store.project(project_id)
        location = (
            value["source"] if value["kind"] == "local"
            else value["document"].get("download", {}).get("local_path")
        )
        if not location or not Path(location).is_file():
            raise HTTPException(404, "Source has not been downloaded or is no longer available.")
        return FileResponse(location)

    @app.get("/api/projects/{project_id}/artifacts/{category}/{filename}")
    def artifact(project_id: str, category: str, filename: str):
        value = store.project(project_id)
        relative = f"{category}/{filename}"
        allowed = {
            entry[key] for entry in value["document"]["exports"]
            for key in ("video", "subtitles")
        }
        directory = store.project_dir(project_id).resolve()
        path = (directory / category / filename).resolve()
        if relative not in allowed or not path.is_relative_to(directory) or not path.is_file():
            raise HTTPException(404, "Artifact is unavailable.")
        media_type = "video/mp4" if path.suffix == ".mp4" else "application/x-subrip"
        return FileResponse(path, media_type=media_type)

    app.mount("/assets", StaticFiles(directory=WEB), name="assets")
    return app
