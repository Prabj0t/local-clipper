from __future__ import annotations

import math
import uuid
from bisect import bisect_left, bisect_right

from .domain import Clip, Cue, Settings


def select_clips(
    cues: list[Cue], energy: list[float], duration: float, settings: Settings,
    replay: list[dict] | None = None, motion: list[float] | None = None,
) -> list[Clip]:
    if duration <= 0:
        return []
    minimum = min(settings.min_duration, duration)
    maximum = min(settings.max_duration, duration)
    cues = sorted(cues, key=lambda cue: cue.start)
    cue_starts = [cue.start for cue in cues]
    cue_ends = sorted(cue.end for cue in cues)
    starts = {max(0, cue.start - 0.25) for cue in cues}
    if energy and max(energy) - min(energy) > 0.001:
        # Local peaks, not raw loudness, keep a constant soundtrack from becoming every highlight.
        for second in range(1, len(energy) - 1):
            if energy[second] > energy[second - 1] and energy[second] >= energy[second + 1]:
                starts.add(max(0, second - minimum * 0.4))
    for second in range(1, len(motion or []) - 1):
        if motion[second] > motion[second - 1] + 0.01 and motion[second] >= motion[second + 1]:
            starts.add(max(0, second - minimum * 0.4))
    if not starts:
        return []
    sorted_energy = sorted(energy)
    baseline = sorted_energy[len(sorted_energy) // 2] if energy else 0
    peak = sorted_energy[int((len(sorted_energy) - 1) * 0.95)] if energy else 0
    candidates = []
    for start in sorted(starts):
        if duration - start < minimum:
            continue
        natural_ends = cue_ends[
            bisect_left(cue_ends, start + minimum):bisect_right(cue_ends, start + maximum)
        ]
        end = natural_ends[-1] if natural_ends else min(duration, start + maximum)
        window = cues[max(0, bisect_right(cue_starts, start) - 1):bisect_left(cue_starts, end)]
        text = " ".join(cue.text for cue in window if cue.end > start)
        interval = energy[int(start):math.ceil(end)]
        change = max(0, (max(interval, default=baseline) - baseline) / max(peak - baseline, 0.001))
        speech = min(1, len(text.split()) / max((end - start) * 1.3, 1))
        complete = float(text.rstrip().endswith((".", "!", "?")))
        replay_value = max(
            (float(point["value"]) for point in (replay or [])
             if point["start_time"] < end and point["end_time"] > start), default=0,
        )
        audio_score = min(1, change) * 0.7
        visual_score = min(1, max((motion or [])[int(start):math.ceil(end)], default=0) * 5) * 0.6
        speech_score = 0.35 * speech + 0.2 * complete
        score = min(
            1, max(audio_score, visual_score) * (0.65 if text else 1)
            + speech_score + 0.2 * replay_value,
        )
        if score < settings.min_score:
            continue
        title = text[:100].strip() or f"Audio reaction at {int(start)}s"
        candidates.append(
            Clip(
                id=uuid.uuid5(uuid.NAMESPACE_URL, f"{start:.3f}:{end:.3f}").hex[:12],
                start=round(start, 3), end=round(end, 3), title=title,
                score=round(score, 3),
                reason="Estimated from speech boundaries and audio/visual variation"
                + ("; public replay signal available" if replay_value else ""),
            )
        )
    selected = []
    for candidate in sorted(candidates, key=lambda item: (-item.score, item.start)):
        if any(
            max(0, min(candidate.end, other.end) - max(candidate.start, other.start))
            / min(candidate.end - candidate.start, other.end - other.start) > 0.25
            for other in selected
        ):
            continue
        selected.append(candidate)
        if settings.max_clips and len(selected) >= settings.max_clips:
            break
        if len(selected) == 100:
            break
    return sorted(selected, key=lambda item: item.start)
