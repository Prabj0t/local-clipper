from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path


class Blocked(Exception):
    """A missing dependency, asset, or permission requires explicit user action."""


class Cancelled(Exception):
    pass


def terminate_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True, timeout=15, check=False,
        )
    else:
        import signal
        try:
            if getattr(process, "_clipper_owns_group", False):
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            import signal
            if getattr(process, "_clipper_owns_group", False):
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        else:
            process.kill()
        process.wait(timeout=5)


def spawn(arguments: list[str], **kwargs) -> subprocess.Popen:
    own_group = os.name != "nt" and os.environ.get("CLIPPER_WORKER") != "1"
    process = subprocess.Popen(
        arguments, shell=False, stdin=subprocess.DEVNULL,
        start_new_session=own_group,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        **kwargs,
    )
    process._clipper_owns_group = own_group
    return process


class Commands:
    def __init__(self, check_cancel, timeout: float):
        self.check_cancel = check_cancel
        self.timeout = timeout

    def run(self, arguments: list[str], log: Path, *, cwd: Path | None = None) -> None:
        self.check_cancel()
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("wb") as output:
            process = spawn(arguments, stdout=output, stderr=subprocess.STDOUT, cwd=cwd)
            deadline = time.monotonic() + self.timeout
            try:
                while process.poll() is None:
                    self.check_cancel()
                    if time.monotonic() > deadline:
                        raise TimeoutError("Processing exceeded its stage timeout.")
                    time.sleep(0.2)
                self.check_cancel()
                if process.returncode:
                    raise RuntimeError(
                        f"{Path(arguments[0]).stem} exited with code {process.returncode}. "
                        f"See {log.name} in this project's logs."
                    )
            finally:
                terminate_tree(process)
