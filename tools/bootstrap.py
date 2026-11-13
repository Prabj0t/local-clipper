from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def confirm(message: str, approved: bool) -> None:
    print(message)
    if not approved and input("Approve this operation? [y/N] ").strip().lower() not in ("y", "yes"):
        raise RuntimeError("Not approved. Nothing was acquired.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Project-local setup and launcher; no silent downloads.")
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--dev", action="store_true")
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--yes", action="store_true", help="Explicitly approve displayed setup operations.")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    interpreter = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if args.check:
        print(json.dumps({
            "python": sys.version.split()[0],
            "project_environment": interpreter.is_file(),
            "network_enabled": False,
        }, indent=2))
        python = str(interpreter) if interpreter.is_file() else sys.executable
        return subprocess.call([python, "-m", "clipper", "--check"], cwd=ROOT)
    if sys.version_info[:2] != (3, 11) or os.name != "nt" or sys.maxsize < 2**32:
        raise RuntimeError("This first release targets Windows x64 with Python 3.11.")
    if args.allow_network and not args.setup:
        raise RuntimeError("--allow-network is accepted only with an explicit --setup request.")
    requirements = ROOT / ("requirements-dev.txt" if args.dev else "requirements.txt")
    lock = ROOT / (
        "requirements-dev-windows-py311.lock" if args.dev else "requirements-windows-py311.lock"
    )
    selected = lock if lock.is_file() else requirements
    if args.setup:
        if not args.wheelhouse and not args.allow_network:
            raise RuntimeError(
                "Setup is offline by default. Supply --wheelhouse with administrator-approved "
                "files, or explicitly approve normal network access with --allow-network. "
                "Do not work around an administrator's restrictions."
            )
        if args.wheelhouse and not args.wheelhouse.is_dir():
            raise ValueError("The approved wheelhouse directory does not exist.")
        print((ROOT / "requirements.txt").read_text("utf-8"))
        if args.dev:
            print(requirements.read_text("utf-8"))
        if lock.is_file():
            print(lock.read_text("utf-8"))
        else:
            print("Development snapshot: an exact release lock has not yet been validated.")
        origin = str(args.wheelhouse.resolve()) if args.wheelhouse else "https://pypi.org/simple"
        confirm(
            f"Install the listed Python packages and their required dependencies into "
            f"{ROOT / '.venv'} from {origin}? This includes packaged VAD assets, but no "
            "Whisper/YuNet weights, FFmpeg, Node.js, Ollama, or videos. "
            "No global packages will be modified.",
            args.yes,
        )
        if not interpreter.is_file():
            subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")], check=True)
        command = [
            str(interpreter), "-m", "pip", "--isolated", "--disable-pip-version-check",
            "install", "--only-binary=:all:", "--no-cache-dir",
            "--retries", "0", "-r", str(selected),
        ]
        if lock.is_file():
            command.append("--require-hashes")
        if args.wheelhouse:
            command.extend(["--no-index", "--find-links", str(args.wheelhouse.resolve())])
        else:
            command.extend(["--index-url", "https://pypi.org/simple"])
        subprocess.run(command, cwd=ROOT, check=True)
        subprocess.run([str(interpreter), "-m", "pip", "check"], cwd=ROOT, check=True)
    if not interpreter.is_file():
        raise RuntimeError("Run Start.ps1 -Setup with an approved package source first.")
    return subprocess.call([str(interpreter), "-m", "clipper", "--port", str(args.port)], cwd=ROOT)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"Setup stopped: {error}", file=sys.stderr)
        print(
            "No mirror, alternate host, certificate bypass, or automatic fallback will be attempted.",
            file=sys.stderr,
        )
        raise SystemExit(1) from error
