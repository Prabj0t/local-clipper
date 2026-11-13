from __future__ import annotations

import array
import json
import math
import subprocess
from pathlib import Path

from .config import Config
from .processes import Blocked, Commands
from .storage import fingerprint


def source_identity(path: Path) -> dict:
    stat = path.stat()
    return {"path": str(path.resolve()), "size": stat.st_size, "modified_ns": stat.st_mtime_ns}


def probe(path: Path, config: Config) -> dict:
    executable = config.tool("ffprobe")
    if not executable:
        raise Blocked("FFprobe is missing. Install an approved FFmpeg build first.")
    if not path.is_file():
        raise Blocked("The selected source file is no longer available.")
    result = subprocess.run(
        [executable, "-v", "error", "-protocol_whitelist", "file,pipe",
         "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True, timeout=30, check=False,
    )
    if result.returncode:
        raise ValueError("FFprobe could not read this source as media.")
    metadata = json.loads(result.stdout)
    video = next((s for s in metadata["streams"] if s["codec_type"] == "video"), None)
    if not video:
        raise ValueError("The source has no video stream.")
    duration = float(metadata.get("format", {}).get("duration") or video.get("duration") or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("A completed recording with a known positive duration is required.")
    width, height = int(video["width"]), int(video["height"])
    sample_aspect = video.get("sample_aspect_ratio", "1:1")
    if sample_aspect and sample_aspect != "N/A":
        numerator, denominator = (int(value) for value in sample_aspect.split(":"))
        if numerator > 0 and denominator > 0:
            width = max(2, round(width * numerator / denominator / 2) * 2)
    rotation = next(
        (float(side["rotation"]) for side in video.get("side_data_list", []) if "rotation" in side),
        float(video.get("tags", {}).get("rotate", 0)),
    )
    if round(rotation) % 180:
        width, height = height, width
    return {
        "duration": duration, "width": width, "height": height,
        "audio": any(s["codec_type"] == "audio" for s in metadata["streams"]),
        "identity": source_identity(path),
    }


def audio_energy(
    path: Path, directory: Path, config: Config, commands: Commands, check_cancel,
) -> list[float]:
    raw = directory / "energy.pcm"
    executable = config.tool("ffmpeg")
    if not executable:
        raise Blocked("FFmpeg is missing.")
    commands.run(
        [executable, "-hide_banner", "-nostdin", "-v", "error", "-y",
         "-protocol_whitelist", "file,pipe", "-i", str(path),
         "-vn", "-ac", "1", "-ar", "2000", "-f", "s16le", str(raw)],
        directory / "logs" / "audio-energy.log",
    )
    values = []
    try:
        with raw.open("rb") as handle:
            while block := handle.read(4000):
                check_cancel()
                samples = array.array("h", block[:len(block) // 2 * 2])
                if samples:
                    values.append(math.sqrt(sum(x * x for x in samples) / len(samples)) / 32768)
    finally:
        raw.unlink(missing_ok=True)
    return values


def visual_activity(
    path: Path, directory: Path, config: Config, commands: Commands, check_cancel,
) -> list[float]:
    raw = directory / "motion.raw"
    executable = config.tool("ffmpeg")
    if not executable:
        raise Blocked("FFmpeg is missing.")
    commands.run(
        [executable, "-hide_banner", "-nostdin", "-v", "error", "-y",
         "-protocol_whitelist", "file,pipe", "-i", str(path),
         "-an", "-vf", "fps=1,scale=96:54,format=gray", "-f", "rawvideo", str(raw)],
        directory / "logs" / "visual-evidence.log",
    )
    values, previous = [], None
    try:
        with raw.open("rb") as handle:
            while frame := handle.read(96 * 54):
                check_cancel()
                if len(frame) != 96 * 54:
                    break
                values.append(
                    sum(abs(a - b) for a, b in zip(previous, frame)) / (96 * 54 * 255)
                    if previous is not None else 0
                )
                previous = frame
    finally:
        raw.unlink(missing_ok=True)
    return values


def render_key(identity: dict, clip: dict, layout: dict, cues: list[dict], preview: bool) -> str:
    return fingerprint(
        {"version": 1, "source": identity, "clip": {key: clip[key] for key in ("start", "end")},
         "layout": layout,
         "cues": cues, "preview": preview}
    )
