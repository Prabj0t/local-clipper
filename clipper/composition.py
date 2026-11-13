from __future__ import annotations

from .domain import Layout, Region
from .processes import Blocked


def _crop(region: Region, source_width: int, source_height: int) -> str:
    width = max(2, int(source_width * region.width) // 2 * 2)
    height = max(2, int(source_height * region.height) // 2 * 2)
    x = min(source_width - width, int(source_width * region.x) // 2 * 2)
    y = min(source_height - height, int(source_height * region.y) // 2 * 2)
    return f"crop={width}:{height}:{x}:{y}"


def _fit(width: int, height: int, mode: str) -> str:
    if mode == "fill":
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=increase:"
            f"force_divisible_by=2,crop={width}:{height}"
        )
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:"
        f"force_divisible_by=2,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
    )


def filter_graph(
    layout: Layout, source_width: int, source_height: int, *,
    preview: bool, captions: bool, tracking: bool = False,
) -> tuple[str, int, int]:
    width = 360 if preview else layout.width
    height = width * 16 // 9
    if layout.mode == "gaming":
        if layout.facecam is None:
            raise Blocked("Confirm the facecam rectangle before rendering a gaming layout.")
        top = round(height * layout.facecam_height / 2) * 2
        graph = (
            "[0:v]setpts=PTS-STARTPTS,scale=trunc(iw*sar/2)*2:ih,setsar=1,"
            "split=2[face][game];"
            f"[face]{_crop(layout.facecam, source_width, source_height)},"
            f"{_fit(width, top, 'fill')}[faceout];"
            f"[game]{_crop(layout.gameplay, source_width, source_height)},"
            f"{_fit(width, height - top, layout.gameplay_fit)}[gameout];"
            "[faceout][gameout]vstack=inputs=2[composed]"
        )
    else:
        crop_width = min(source_width, int(source_height * 9 / 16) // 2 * 2)
        crop_height = min(source_height, int(source_width * 16 / 9) // 2 * 2)
        x = max(0, min(source_width - crop_width, source_width * layout.focus_x - crop_width / 2))
        y = max(0, (source_height - crop_height) // 2)
        commands = "sendcmd=f=tracking.txt," if tracking else ""
        graph = (
            "[0:v]setpts=PTS-STARTPTS,scale=trunc(iw*sar/2)*2:ih,setsar=1,"
            f"{commands}crop@subject={crop_width}:{crop_height}:{int(x)}:{y},"
            f"scale={width}:{height}[composed]"
        )
    graph += (
        ";[composed]ass=captions.ass,format=yuv420p[out]"
        if captions else ";[composed]format=yuv420p[out]"
    )
    return graph, width, height
