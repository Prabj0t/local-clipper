from __future__ import annotations

import json
from pathlib import Path

from .config import Config
from .domain import Cue, Settings, Word
from .media import source_identity
from .processes import Blocked, Commands
from .storage import atomic_json, fingerprint


def transcribe(
    source: Path, duration: float, directory: Path, settings: Settings, config: Config,
    commands: Commands, check_cancel, report,
) -> list[Cue]:
    model_path = config.models_dir / f"whisper-{settings.speech_model}"
    required = ("model.bin", "config.json", "tokenizer.json")
    if not all((model_path / name).is_file() for name in required):
        raise Blocked(
            f"Whisper {settings.speech_model} is not available locally. "
            "Obtain explicit approval before installing its weights, or disable automatic captions."
        )
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise Blocked("The optional speech runtime is not installed.") from exc
    key = fingerprint(
        {"version": 1, "source": source_identity(source), "model": [
            source_identity(model_path / name) for name in required
        ], "captions": "english-translation"}
    )
    cache = directory / "cache" / f"speech-{key}"
    cache.mkdir(parents=True, exist_ok=True)
    model = None
    cues = []
    try:
        for index, owner_start in enumerate(range(0, int(duration) + 1, 600)):
            if owner_start >= duration:
                break
            check_cancel()
            part = cache / f"part-{index:05}.json"
            if part.is_file():
                try:
                    saved = [
                        Cue.model_validate(item) for item in json.loads(part.read_text("utf-8"))
                    ]
                    cues.extend(saved)
                    continue
                except (ValueError, TypeError, OSError):
                    pass
            report("transcribing", 0.1 + 0.4 * owner_start / duration)
            if model is None:
                model = WhisperModel(
                    str(model_path), device="cpu", compute_type="int8",
                    cpu_threads=2, num_workers=1, local_files_only=True,
                )
            start = max(0, owner_start - 2)
            end = min(duration, owner_start + 602)
            wave = cache / f"part-{index:05}.wav"
            try:
                commands.run(
                    [config.tool("ffmpeg"), "-hide_banner", "-nostdin", "-v", "error", "-y",
                     "-ss", str(start), "-protocol_whitelist", "file,pipe",
                     "-i", str(source), "-t", str(end - start),
                     "-vn", "-ac", "1", "-ar", "16000", str(wave)],
                    directory / "logs" / "speech-audio.log",
                )
                segments, info = model.transcribe(
                    str(wave), task="transcribe", word_timestamps=True, vad_filter=True, beam_size=3,
                )
                english = info.language == "en"
                if not english:
                    segments.close()
                    segments, _ = model.transcribe(
                        str(wave), task="translate", language=info.language,
                        word_timestamps=False, vad_filter=True, beam_size=3,
                    )
                chunk = []
                for segment in segments:
                    check_cancel()
                    absolute_start = start + segment.start
                    if not owner_start <= absolute_start < min(duration, owner_start + 600):
                        continue
                    if not segment.text.strip() or segment.end <= segment.start:
                        continue
                    words = []
                    if english:
                        words = [
                            Word(
                                start=max(absolute_start, start + word.start),
                                end=min(duration, start + segment.end, start + word.end),
                                text=word.word.strip(),
                            )
                            for word in (segment.words or [])
                            if word.word.strip() and word.end > word.start
                            and start + word.start < duration
                            and min(duration, start + segment.end, start + word.end)
                            > max(absolute_start, start + word.start)
                        ]
                    if words:
                        for offset in range(0, len(words), 7):
                            group = words[offset:offset + 7]
                            chunk.append(
                                Cue(start=group[0].start, end=group[-1].end,
                                    text=" ".join(word.text for word in group), words=group)
                            )
                    else:
                        chunk.append(
                            Cue(start=absolute_start, end=min(duration, start + segment.end),
                                text=segment.text.strip()[:1000])
                        )
                atomic_json(part, [cue.model_dump() for cue in chunk])
                cues.extend(chunk)
            finally:
                wave.unlink(missing_ok=True)
    finally:
        del model
    return sorted(cues, key=lambda cue: cue.start)
