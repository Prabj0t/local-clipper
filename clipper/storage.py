from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .domain import JobRequest, ProjectCreate, ProjectEdit, Status, canonical_source


class Conflict(Exception):
    pass


def now() -> str:
    return datetime.now(UTC).isoformat()


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:24]


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.path = self.root / "state.sqlite3"

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("PRAGMA synchronous=FULL")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise RuntimeError("This database requires a newer application version.")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
                    source TEXT NOT NULL, settings TEXT NOT NULL, document TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    action TEXT NOT NULL, status TEXT NOT NULL, request TEXT NOT NULL,
                    stage TEXT NOT NULL DEFAULT 'queued', progress REAL NOT NULL DEFAULT 0,
                    error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS active_project_job
                    ON jobs(project_id)
                    WHERE status IN ('queued', 'running', 'cancelling');
                CREATE INDEX IF NOT EXISTS queue_order ON jobs(status, created_at);
                PRAGMA user_version=1;
            """)

    def project_dir(self, project_id: str) -> Path:
        uuid.UUID(project_id)
        return self.root / "projects" / project_id

    @staticmethod
    def _project(row) -> dict:
        if row is None:
            raise KeyError("Project not found.")
        value = dict(row)
        value["settings"] = json.loads(value["settings"])
        value["document"] = json.loads(value["document"])
        return value

    def create(self, request: ProjectCreate) -> dict:
        kind, source = canonical_source(request.source)
        project_id = str(uuid.uuid4())
        timestamp = now()
        with self.connection() as db:
            db.execute(
                "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
                (
                    project_id, request.name, kind, source,
                    request.settings.model_dump_json(),
                    json.dumps({"clips": [], "cues": [], "exports": [], "warnings": []}),
                    timestamp, timestamp,
                ),
            )
        return self.project(project_id)

    def project(self, project_id: str) -> dict:
        with self.connection() as db:
            return self._project(
                db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            )

    def projects(self) -> list[dict]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT id, name, kind, revision, created_at, updated_at "
                "FROM projects ORDER BY updated_at DESC LIMIT 100"
            ).fetchall()
            return [dict(row) for row in rows]

    def edit(self, project_id: str, edit: ProjectEdit) -> dict:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            project = self._project(row)
            if project["revision"] != edit.revision:
                raise Conflict("Project changed. Reload before saving.")
            if db.execute(
                "SELECT 1 FROM jobs WHERE project_id=? "
                "AND status IN ('queued','running','cancelling')", (project_id,),
            ).fetchone():
                raise Conflict("Cancel or finish the active job before editing.")
            document = project["document"]
            duration = document.get("media", {}).get("duration")
            if duration and any(item.end > duration + 0.05 for item in [*edit.clips, *edit.cues]):
                raise ValueError("Clip and caption times must stay within the source duration.")
            document.update(
                clips=[clip.model_dump() for clip in edit.clips],
                cues=[cue.model_dump() for cue in edit.cues],
                exports=[],
            )
            db.execute(
                "UPDATE projects SET settings=?, document=?, revision=revision+1, "
                "updated_at=? WHERE id=?",
                (edit.settings.model_dump_json(), json.dumps(document), now(), project_id),
            )
        return self.project(project_id)

    def publish(self, project_id: str, document: dict) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE projects SET document=?, revision=revision+1, updated_at=? WHERE id=?",
                (json.dumps(document, allow_nan=False), now(), project_id),
            )

    def enqueue(self, project_id: str, request: JobRequest) -> dict:
        self.project(project_id)
        job_id, timestamp = str(uuid.uuid4()), now()
        try:
            with self.connection() as db:
                db.execute(
                    "INSERT INTO jobs(id, project_id, action, status, request, created_at, "
                    "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (job_id, project_id, request.action, Status.QUEUED,
                     request.model_dump_json(), timestamp, timestamp),
                )
        except sqlite3.IntegrityError as exc:
            raise Conflict("This project already has an active job.") from exc
        return self.job(job_id)

    def job(self, job_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError("Job not found.")
        value = dict(row)
        value["request"] = json.loads(value["request"])
        return value

    def jobs(self, project_id: str) -> list[dict]:
        with self.connection() as db:
            return [
                dict(row) for row in db.execute(
                    "SELECT id, action, status, stage, progress, error, created_at, updated_at "
                    "FROM jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 20",
                    (project_id,),
                )
            ]

    def claim(self) -> dict | None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if not row:
                return None
            db.execute(
                "UPDATE jobs SET status='running', stage='starting', updated_at=? WHERE id=?",
                (now(), row["id"]),
            )
        return self.job(row["id"])

    def progress(self, job_id: str, stage: str, progress: float) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE jobs SET stage=?, progress=?, updated_at=? WHERE id=? AND status='running'",
                (stage, max(0, min(1, progress)), now(), job_id),
            )

    def finish(self, job_id: str, status: Status, error: str | None = None) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE jobs SET status=CASE WHEN status='cancelling' THEN 'cancelled' ELSE ? END, "
                "stage=?, error=?, progress=CASE WHEN ?='succeeded' THEN 1 ELSE progress END, "
                "updated_at=? WHERE id=? AND status IN ('running','cancelling')",
                (status, status, error, status, now(), job_id),
            )

    def cancel(self, job_id: str) -> dict:
        self.job(job_id)
        with self.connection() as db:
            db.execute(
                "UPDATE jobs SET status=CASE WHEN status='queued' THEN 'cancelled' "
                "ELSE 'cancelling' END, updated_at=? WHERE id=? AND status IN ('queued','running')",
                (now(), job_id),
            )
        return self.job(job_id)

    def cancelled(self, job_id: str) -> bool:
        return self.job(job_id)["status"] not in (Status.RUNNING, Status.QUEUED)

    def recover(self) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE jobs SET status='interrupted', stage='interrupted', "
                "error='Application stopped. Retry to reuse completed checkpoints.', updated_at=? "
                "WHERE status IN ('running','cancelling')", (now(),),
            )


class InstanceLock:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / ".instance.lock"
        self.handle = None

    def acquire(self) -> None:
        self.handle = self.path.open("a+b")
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise RuntimeError("Another instance is already using this data directory.") from exc

    def release(self) -> None:
        if self.handle:
            self.handle.close()
            self.handle = None
