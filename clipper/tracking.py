from __future__ import annotations

from pathlib import Path

from .config import Config
from .domain import Clip, Layout
from .processes import Blocked


def smooth_centers(
    samples: list[tuple[float, float | None, bool]], initial: float,
) -> list[tuple[float, float]]:
    result, current, previous_time = [], initial, 0.0
    for timestamp, detected, scene_cut in samples:
        delta_time = max(0, timestamp - previous_time)
        if detected is not None:
            target = max(0.0, min(1.0, detected))
            if scene_cut:
                current = target
            elif abs(target - current) > 0.04:
                move = (target - current) * 0.45
                limit = 0.12 * delta_time
                current += max(-limit, min(limit, move))
        result.append((timestamp, current))
        previous_time = timestamp
    return result


def tracking_commands(
    source: Path, clip: Clip, layout: Layout, media: dict, config: Config, check_cancel,
) -> str:
    model_path = config.models_dir / "yunet.onnx"
    if not model_path.is_file():
        raise Blocked("Face following requires an explicitly acquired local YuNet model.")
    try:
        import cv2
    except ImportError as exc:
        raise Blocked("The optional OpenCV runtime is not installed.") from exc
    detector = cv2.FaceDetectorYN.create(str(model_path), "", (320, 320), 0.8)
    capture = cv2.VideoCapture(str(source))
    samples, previous_gray, previous_center = [], None, layout.focus_x
    try:
        if not capture.isOpened():
            raise ValueError("Unable to open the source for face tracking.")
        for step in range(int((clip.end - clip.start) * 2) + 1):
            check_cancel()
            timestamp = min(step / 2, clip.end - clip.start)
            capture.set(cv2.CAP_PROP_POS_MSEC, (clip.start + timestamp) * 1000)
            ok, frame = capture.read()
            if not ok:
                break
            h, w = frame.shape[:2]
            resized = cv2.resize(frame, (320, max(2, round(320 * h / w))))
            detector.setInputSize((resized.shape[1], resized.shape[0]))
            _, faces = detector.detect(resized)
            center = None
            if faces is not None and len(faces):
                choices = [(float(face[0] + face[2] / 2) / resized.shape[1]) for face in faces]
                center = min(choices, key=lambda candidate: abs(candidate - previous_center))
                previous_center = center
            gray = cv2.resize(cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY), (32, 18))
            cut = previous_gray is not None and float(cv2.absdiff(gray, previous_gray).mean()) > 45
            samples.append((timestamp, center, cut))
            previous_gray = gray
    finally:
        capture.release()
    points = smooth_centers(samples, layout.focus_x)
    width = media["width"]
    crop_width = min(width, int(media["height"] * 9 / 16) // 2 * 2)
    positions = [
        (time, max(0, min(width - crop_width, center * width - crop_width / 2)))
        for time, center in points
    ]
    lines = []
    for index, ((time, x), (next_time, next_x)) in enumerate(zip(positions, positions[1:])):
        if next_time > time:
            if samples[index + 1][2]:
                next_x = x
            expression = f"{x:.3f}+({next_x-x:.3f})*clip((t-{time:.3f})/{next_time-time:.3f},0,1)"
            lines.append(f"{time:.3f} crop@subject x '{expression}';")
    if not positions:
        raise Blocked("No frames could be decoded for face following. Use manual framing.")
    lines.append(f"{positions[-1][0]:.3f} crop@subject x '{positions[-1][1]:.3f}';")
    return "\n".join(lines)
