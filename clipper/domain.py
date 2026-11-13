from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

Unit = Annotated[float, Field(ge=0, le=1)]
Time = Annotated[float, Field(ge=0)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class Action(StrEnum):
    INSPECT = "inspect"
    ANALYZE = "analyze"
    PREVIEW = "preview"
    RENDER = "render"


class Status(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    INTERRUPTED = "interrupted"


ACTIVE = (Status.QUEUED, Status.RUNNING, Status.CANCELLING)


class Region(Contract):
    x: Unit = 0
    y: Unit = 0
    width: Annotated[float, Field(gt=0, le=1)] = 1
    height: Annotated[float, Field(gt=0, le=1)] = 1

    @model_validator(mode="after")
    def within_frame(self) -> Self:
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("Region must stay inside the source frame.")
        return self


class Layout(Contract):
    mode: Literal["podcast", "gaming"] = "podcast"
    gameplay: Region = Field(default_factory=Region)
    facecam: Region | None = None
    gameplay_fit: Literal["fit", "fill"] = "fit"
    facecam_height: Annotated[float, Field(ge=0.15, le=0.5)] = 0.3
    focus_x: Unit = 0.5
    follow_face: bool = False
    width: Literal[720, 1080] = 720


class Settings(Contract):
    min_duration: Annotated[float, Field(ge=5, le=600)] = 20
    max_duration: Annotated[float, Field(ge=5, le=600)] = 60
    max_clips: Annotated[int, Field(ge=1, le=100)] | None = None
    min_score: Unit = 0.55
    captions: bool = True
    speech_model: Literal["small", "medium"] = "small"
    auto_export: bool = False
    llm: Literal["off", "qwen3:1.7b", "qwen3:4b"] = "off"
    source_height: Literal[720, 1080] = 1080
    layout: Layout = Field(default_factory=Layout)

    @model_validator(mode="after")
    def duration_order(self) -> Self:
        if self.min_duration > self.max_duration:
            raise ValueError("Minimum clip length cannot exceed maximum clip length.")
        return self


class Word(Contract):
    start: Time
    end: Time
    text: Annotated[str, Field(min_length=1, max_length=200)]

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.end <= self.start:
            raise ValueError("End must be after start.")
        return self


class Cue(Contract):
    start: Time
    end: Time
    text: Annotated[str, Field(min_length=1, max_length=1000)]
    words: list[Word] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.end <= self.start:
            raise ValueError("Caption end must be after start.")
        if any(w.start < self.start - 0.1 or w.end > self.end + 0.1 for w in self.words):
            raise ValueError("Word timestamps must stay within the caption.")
        return self


class Clip(Contract):
    id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")]
    start: Time
    end: Time
    title: Annotated[str, Field(min_length=1, max_length=160)] = "Highlight"
    score: Unit = 0
    reason: Annotated[str, Field(max_length=1000)] = "Manually selected"
    selected: bool = True

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.end <= self.start or self.end - self.start > 600:
            raise ValueError("A clip must have positive duration and be at most 10 minutes.")
        return self


class ProjectCreate(Contract):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    source: Annotated[str, Field(min_length=1, max_length=4096)]
    settings: Settings = Field(default_factory=Settings)


class ProjectEdit(Contract):
    revision: Annotated[int, Field(ge=0)]
    settings: Settings
    clips: list[Clip] = Field(default_factory=list, max_length=100)
    cues: list[Cue] = Field(default_factory=list, max_length=50000)

    @model_validator(mode="after")
    def unique_clips(self) -> Self:
        if len({clip.id for clip in self.clips}) != len(self.clips):
            raise ValueError("Clip IDs must be unique.")
        return self


class JobRequest(Contract):
    action: Action
    download_approved: bool = False


def canonical_source(value: str) -> tuple[str, str]:
    url = urlparse(value)
    if url.scheme in ("http", "https"):
        if url.scheme != "https" or url.username or url.password or url.port is not None:
            raise ValueError("Use an HTTPS YouTube video URL without credentials or a port.")
        query = parse_qs(url.query)
        if "list" in query:
            raise ValueError("Use a single video URL without a playlist parameter.")
        host = (url.hostname or "").lower()
        if host == "youtu.be":
            video_id = url.path.strip("/")
        elif host in ("youtube.com", "www.youtube.com", "m.youtube.com"):
            if url.path == "/watch":
                video_id = query.get("v", [""])[0]
            elif url.path.startswith(("/shorts/", "/live/")):
                video_id = url.path.split("/")[2]
            else:
                video_id = ""
        else:
            raise ValueError("Only YouTube video URLs are supported.")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{11}", video_id):
            raise ValueError("The URL must identify one YouTube video.")
        return "youtube", f"https://www.youtube.com/watch?v={video_id}"
    if value.startswith(("\\\\", "//")):
        raise ValueError("Choose a local file, not a network share.")
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise ValueError("Enter the full path of an existing local video.")
    if path.suffix.lower() not in {".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi"}:
        raise ValueError("Unsupported video extension.")
    return "local", str(path.resolve())
