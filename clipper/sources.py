from __future__ import annotations

from pathlib import Path

from .config import Config
from .domain import Settings
from .processes import Blocked


def youtube(
    source: str, directory: Path, settings: Settings, config: Config,
    *, download: bool, approved: bool, check_cancel,
) -> dict:
    if download and not approved:
        raise Blocked("Explicit approval is required to download this YouTube video.")
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise Blocked("The optional YouTube runtime is not installed. No download was attempted.") from exc

    def progress(_event):
        check_cancel()

    options = {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 20,
        "retries": 2,
        "fragment_retries": 2,
        "concurrent_fragment_downloads": 1,
        "geo_bypass": False,
        "remote_components": set(),
        "js_runtimes": {"node": {"path": config.tool("node")}} if config.tool("node") else {},
        "progress_hooks": [progress],
        "format": (
            f"bestvideo[height<={settings.source_height}]+bestaudio/"
            f"best[height<={settings.source_height}]"
        ),
        "merge_output_format": "mp4",
        "outtmpl": str(directory / "source.%(ext)s"),
    }
    if config.tool("ffmpeg"):
        options["ffmpeg_location"] = str(Path(config.tool("ffmpeg")).parent)
    if download:
        directory.mkdir(parents=True, exist_ok=True)
    check_cancel()
    with YoutubeDL(options) as client:
        info = client.extract_info(source, download=False)
        if not info or info.get("_type") in ("playlist", "multi_video"):
            raise ValueError("A single completed video is required.")
        if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
            raise ValueError("Ongoing or upcoming live streams are not supported.")
        if download:
            if not config.tool("ffmpeg"):
                raise Blocked("Install an approved FFmpeg build before downloading video.")
            check_cancel()
            client.process_info(info)
    result = {
        "title": info.get("title", "YouTube recording"),
        "duration": info.get("duration"),
        "estimated_bytes": info.get("filesize") or info.get("filesize_approx"),
        "replay": [
            item for item in (info.get("heatmap") or [])
            if isinstance(item, dict)
            and all(isinstance(item.get(key), (int, float))
                    for key in ("start_time", "end_time", "value"))
            and 0 <= item["value"] <= 1
            and 0 <= item["start_time"] < item["end_time"]
        ],
    }
    if download:
        candidates = [
            path for path in directory.glob("source.*")
            if path.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}
        ]
        if len(candidates) != 1:
            raise RuntimeError("The download did not produce exactly one merged video.")
        result["local_path"] = str(candidates[0].resolve())
    return result
