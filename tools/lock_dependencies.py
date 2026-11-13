from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

ROOT = Path(__file__).resolve().parent.parent


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve pinned dependencies from approved local wheels only.")
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--development", action="store_true")
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 11) or os.name != "nt" or sys.maxsize < 2**32:
        raise RuntimeError("Generate this release lock with Windows x64 Python 3.11.")
    wheelhouse = args.wheelhouse.resolve()
    if not wheelhouse.is_dir():
        raise ValueError("The approved wheelhouse does not exist.")
    with tempfile.TemporaryDirectory(prefix="clipper-resolve-") as temporary:
        report = Path(temporary) / "resolve.json"
        command = [
            sys.executable, "-m", "pip", "--isolated", "--disable-pip-version-check",
            "install", "--dry-run", "--ignore-installed", "--no-cache-dir", "--no-index",
            "--find-links", str(wheelhouse), "--only-binary=:all:", "--report", str(report),
            "-r", str(ROOT / "requirements.txt"),
        ]
        if args.development:
            command.extend(["-r", str(ROOT / "requirements-dev.txt")])
        subprocess.run(command, check=True, cwd=ROOT)
        packages = json.loads(report.read_text("utf-8"))["install"]
        lines = ["# Generated from an administrator-approved local wheelhouse. Windows x64 / Python 3.11."]
        for package in sorted(packages, key=lambda item: item["metadata"]["name"].lower()):
            url = urlparse(package["download_info"]["url"])
            if url.scheme != "file" or url.netloc:
                raise RuntimeError("The resolver referenced a non-local artifact.")
            path = Path(url2pathname(url.path)).resolve()
            if not path.is_relative_to(wheelhouse) or path.suffix != ".whl" or path.is_symlink():
                raise RuntimeError("The resolver referenced an artifact outside the approved wheelhouse.")
            metadata = package["metadata"]
            lines.append(f"{metadata['name']}=={metadata['version']} --hash=sha256:{sha256(path)}")
        filename = (
            "requirements-dev-windows-py311.lock" if args.development
            else "requirements-windows-py311.lock"
        )
        (ROOT / filename).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Wrote {filename}; no package or model was downloaded.")


if __name__ == "__main__":
    main()
