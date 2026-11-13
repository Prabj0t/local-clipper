from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from .config import Config


def main() -> None:
    parser = argparse.ArgumentParser(description="Original, local-first 9:16 video clipper.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--check", action="store_true", help="Report local capabilities; never download.")
    args = parser.parse_args()
    config = Config.from_env()
    if args.data_dir:
        config = replace(config, data_dir=args.data_dir.resolve())
    if args.check:
        print(json.dumps(config.capabilities(), indent=2))
        return
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535.")
    try:
        import uvicorn
        from .api import create_app
    except ModuleNotFoundError as exc:
        raise SystemExit(
            f"Missing dependency: {exc.name}. See README.md for approved, project-local setup. "
            "No installation or download was attempted."
        ) from exc
    uvicorn.run(
        create_app(config), host="127.0.0.1", port=args.port,
        workers=1, proxy_headers=False, access_log=False,
    )


if __name__ == "__main__":
    main()
