from __future__ import annotations

import importlib.util
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    data_dir: Path = ROOT / ".data"
    models_dir: Path = ROOT / ".models"
    tools_dir: Path = ROOT / ".tools"
    worker_enabled: bool = True
    process_timeout: float = 6 * 60 * 60

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            data_dir=Path(os.environ.get("CLIPPER_DATA_DIR", ROOT / ".data")).resolve(),
            models_dir=Path(os.environ.get("CLIPPER_MODELS_DIR", ROOT / ".models")).resolve(),
            tools_dir=Path(os.environ.get("CLIPPER_TOOLS_DIR", ROOT / ".tools")).resolve(),
        )

    def tool(self, name: str) -> str | None:
        suffix = ".exe" if os.name == "nt" else ""
        local = (
            self.tools_dir / "node" / f"{name}{suffix}"
            if name == "node" else self.tools_dir / "ffmpeg" / "bin" / f"{name}{suffix}"
        )
        return str(local) if local.is_file() else shutil.which(name)

    def capabilities(self) -> dict:
        return {
            "api_runtime": all(
                importlib.util.find_spec(name) is not None
                for name in ("fastapi", "uvicorn", "pydantic")
            ),
            "ffmpeg": bool(self.tool("ffmpeg")),
            "ffprobe": bool(self.tool("ffprobe")),
            "speech_runtime": importlib.util.find_spec("faster_whisper") is not None,
            "vision_runtime": importlib.util.find_spec("cv2") is not None,
            "youtube_runtime": importlib.util.find_spec("yt_dlp") is not None,
            "javascript_runtime": bool(self.tool("node")),
            "speech_models": [
                name
                for name in ("small", "medium")
                if all(
                    (self.models_dir / f"whisper-{name}" / file).is_file()
                    for file in ("model.bin", "config.json", "tokenizer.json")
                )
            ],
            "face_model": (self.models_dir / "yunet.onnx").is_file(),
            "downloads_automatic": False,
        }
