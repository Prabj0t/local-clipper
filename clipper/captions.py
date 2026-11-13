from __future__ import annotations

from .domain import Clip, Cue, Word


def _clock(seconds: float, *, ass: bool = False) -> str:
    scale = 100 if ass else 1000
    ticks = round(max(0, seconds) * scale)
    whole, fraction = divmod(ticks, scale)
    minutes, second = divmod(whole, 60)
    hour, minute = divmod(minutes, 60)
    if ass:
        return f"{hour}:{minute:02}:{second:02}.{fraction:02}"
    return f"{hour:02}:{minute:02}:{second:02},{fraction:03}"


def clip_cues(cues: list[Cue], clip: Clip) -> list[Cue]:
    result = []
    for cue in sorted(cues, key=lambda value: value.start):
        start, end = max(cue.start, clip.start), min(cue.end, clip.end)
        if end > start:
            result.append(
                Cue(
                    start=start - clip.start, end=end - clip.start, text=cue.text,
                    words=[
                        Word(
                            start=max(start, word.start) - clip.start,
                            end=min(end, word.end) - clip.start, text=word.text,
                        )
                        for word in cue.words if min(end, word.end) > max(start, word.start)
                    ],
                )
            )
    return result


def srt(cues: list[Cue]) -> str:
    return "\n\n".join(
        f"{index}\n{_clock(cue.start)} --> {_clock(cue.end)}\n{cue.text}"
        for index, cue in enumerate(cues, 1)
    ) + ("\n" if cues else "")


def _escape(text: str) -> str:
    # Untrusted caption text must never become ASS override tags or drawing commands.
    return text.replace("\\", "\uff3c").replace("{", "\uff5b").replace("}", "\uff5d").replace(
        "\r", ""
    ).replace("\n", r"\N")


def ass(cues: list[Cue], width: int, height: int) -> str:
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,Arial,{round(width * 0.055)},&H00FFFFFF,&H0000FFFF,&H00101010,"
        f"&H80000000,-1,0,0,0,100,100,0,0,1,2,1,2,{round(width * .06)},"
        f"{round(width * .06)},{round(height * .1)},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = []
    for cue in cues:
        if not cue.words:
            parts = [(cue.start, cue.end, _escape(cue.text))]
        else:
            boundaries = sorted({cue.start, cue.end, *(
                point for word in cue.words for point in (word.start, word.end)
            )})
            parts = []
            for start, end in zip(boundaries, boundaries[1:]):
                midpoint = (start + end) / 2
                text = " ".join(
                    (r"{\c&H00E3F7A7&}" + _escape(word.text) + r"{\c&H00FFFFFF&}")
                    if word.start <= midpoint < word.end else _escape(word.text)
                    for word in cue.words
                )
                parts.append((start, end, text))
        events.extend(
            f"Dialogue: 0,{_clock(start, ass=True)},{_clock(end, ass=True)},"
            f"Default,,0,0,0,,{text}\n"
            for start, end, text in parts if _clock(start, ass=True) != _clock(end, ass=True)
        )
    return header + "".join(events)
