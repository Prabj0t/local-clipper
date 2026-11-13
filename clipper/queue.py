from __future__ import annotations

import logging
import os
import sys
import threading
import time

from .config import ROOT, Config
from .domain import Status
from .processes import spawn, terminate_tree
from .storage import Store


class Coordinator:
    def __init__(self, config: Config, store: Store):
        self.config, self.store = config, store
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="clipper-queue", daemon=True)

    def start(self) -> None:
        self.store.recover()
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=25)
        if self.thread.is_alive():
            raise RuntimeError("The processing worker did not stop cleanly.")

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            job = self.store.claim()
            if job is None:
                self.stop_event.wait(0.5)
                continue
            log = self.store.project_dir(job["project_id"]) / "logs" / f"{job['id']}.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            environment = {
                **os.environ,
                "CLIPPER_MODELS_DIR": str(self.config.models_dir),
                "CLIPPER_TOOLS_DIR": str(self.config.tools_dir),
                "HF_HUB_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "CLIPPER_WORKER": "1",
            }
            try:
                with log.open("ab") as output:
                    process = spawn(
                        [sys.executable, "-m", "clipper.worker", "--data-dir",
                         str(self.config.data_dir), "--job-id", job["id"]],
                        stdout=output, stderr=output, cwd=ROOT, env=environment,
                    )
                    try:
                        cancellation_started = None
                        while process.poll() is None:
                            if self.stop_event.wait(0.25):
                                break
                            if self.store.cancelled(job["id"]):
                                cancellation_started = cancellation_started or time.monotonic()
                                if time.monotonic() - cancellation_started > 5:
                                    break
                    finally:
                        terminate_tree(process)
                current = self.store.job(job["id"])
                if current["status"] == Status.CANCELLING:
                    self.store.finish(job["id"], Status.CANCELLED, "Cancelled by the user.")
                elif current["status"] == Status.RUNNING:
                    state = Status.INTERRUPTED if self.stop_event.is_set() else Status.FAILED
                    self.store.finish(job["id"], state, "Worker stopped before completing the job.")
            except Exception:
                logging.exception("Worker coordination failed for job %s", job["id"])
                self.store.finish(job["id"], Status.FAILED, "Unable to start or monitor the worker.")
