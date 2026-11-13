from __future__ import annotations

import argparse
import json
import traceback
from dataclasses import replace
from pathlib import Path

from .config import Config
from .domain import Status
from .pipeline import Pipeline
from .processes import Blocked, Cancelled
from .storage import Store, now


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()
    config = replace(Config.from_env(), data_dir=args.data_dir.resolve())
    store = Store(config.data_dir)
    job = store.job(args.job_id)
    if job["status"] != Status.RUNNING:
        return
    print(json.dumps({"time": now(), "event": "job_started", "job_id": job["id"]}), flush=True)
    try:
        Pipeline(config, store, job).run()
        store.finish(job["id"], Status.SUCCEEDED)
    except Cancelled:
        store.finish(job["id"], Status.CANCELLED, "Cancelled by the user.")
    except Blocked as exc:
        store.finish(job["id"], Status.BLOCKED, str(exc))
    except (ValueError, TimeoutError, RuntimeError) as exc:
        store.finish(job["id"], Status.FAILED, str(exc))
        traceback.print_exc()
    except Exception:
        store.finish(job["id"], Status.FAILED, "Unexpected processing error. See the local job log.")
        traceback.print_exc()
    print(json.dumps({
        "time": now(), "event": "job_finished", "job_id": job["id"],
        "status": store.job(job["id"])["status"],
    }), flush=True)


if __name__ == "__main__":
    main()
