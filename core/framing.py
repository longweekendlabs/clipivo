"""Output size and person framing.

Size limits apply to the shorter side, so any shape (16:9, 4:3, vertical) keeps its
proportions and nothing is ever enlarged. Person framing picks one steady crop per clip
around where the person's face sat, then scales every clip to the same output size so a
stitched video never changes size.
"""

from __future__ import annotations

from statistics import median

MAX_SIZES = {0: "Original size", 1440: "Up to 1440p", 1080: "Up to 1080p", 720: "Up to 720p"}
FRAMINGS = {
    "whole": "Whole picture",
    "landscape": "Follow person · landscape",
    "portrait": "Follow person · portrait",
    "square": "Follow person · square",
}
_ASPECT = {"landscape": 16 / 9, "portrait": 9 / 16, "square": 1.0}
# Crop height in face heights: enough for head and shoulders, tighter for landscape so a
# split screen's other half stays out.
_HEIGHT_IN_FACES = {"landscape": 3.6, "portrait": 5.5, "square": 3.6}
DEFAULT_CROP_SHORT_SIDE = 1080


def subject_from_hits(hits) -> list[float] | None:
    """Summarise face boxes from hits inside one clip: [x_min, x_max, y, face_h, frame_w, frame_h]."""
    boxes = [(hit.box, hit.frame_size) for hit in hits if getattr(hit, "box", None) and getattr(hit, "frame_size", None)]
    if not boxes:
        return None
    centres_x = [(box[0] + box[2]) / 2 for box, _size in boxes]
    centres_y = [(box[1] + box[3]) / 2 for box, _size in boxes]
    heights = [box[3] - box[1] for box, _size in boxes]
    frame_w, frame_h = boxes[0][1]
    return [min(centres_x), max(centres_x), median(centres_y), median(heights), float(frame_w), float(frame_h)]


def scale_filter(max_short_side: int) -> str:
    """Limit the shorter side, keep the shape, never enlarge."""
    n = int(max_short_side)
    return (
        f"scale='if(gte(iw,ih),-2,min(iw,{n}))':'if(gte(iw,ih),min(ih,{n}),-2)'"
        ":flags=lanczos,setsar=1"
    )


def output_size(framing: str, max_short_side: int) -> tuple[int, int]:
    short = int(max_short_side) or DEFAULT_CROP_SHORT_SIDE
    aspect = _ASPECT[framing]
    if aspect >= 1:
        return _even(short * aspect), _even(short)
    return _even(short), _even(short / aspect)


def crop_box(subject: list[float] | None, framing: str, frame_size: tuple[int, int]) -> tuple[int, int, int, int]:
    """Steady crop (x, y, w, h) around the person for one clip; centred when nothing is known."""
    frame_w, frame_h = frame_size
    aspect = _ASPECT[framing]
    if not subject:
        height = min(frame_h, frame_w / aspect)
        width = height * aspect
        return _clamped((frame_w - width) / 2, (frame_h - height) / 2, width, height, frame_w, frame_h)
    x_min, x_max, centre_y, face_h, sub_w, sub_h = subject
    # Subjects are measured on the decoded frame; rescale if the export frame differs.
    sx, sy = frame_w / max(1.0, sub_w), frame_h / max(1.0, sub_h)
    x_min, x_max, centre_y, face_h = x_min * sx, x_max * sx, centre_y * sy, face_h * sy
    height = face_h * _HEIGHT_IN_FACES[framing]
    width = height * aspect
    # If the person moved around within the clip, widen enough to keep every position in view.
    needed = (x_max - x_min) + face_h * 1.2
    if width < needed:
        width = needed
        height = width / aspect
    if height > frame_h:
        height = frame_h
        width = height * aspect
    if width > frame_w:
        width = frame_w
        height = width / aspect
    centre_x = (x_min + x_max) / 2
    top = centre_y - face_h * 1.1  # a little headroom above the face
    x = min(max(0.0, centre_x - width / 2), frame_w - width)
    y = min(max(0.0, top), frame_h - height)
    return _clamped(x, y, width, height, frame_w, frame_h)


def _clamped(x: float, y: float, width: float, height: float, frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
    w = min(_even(width), frame_w - frame_w % 2)
    h = min(_even(height), frame_h - frame_h % 2)
    return min(_even(x), frame_w - w), min(_even(y), frame_h - h), w, h


def _even(value: float) -> int:
    return max(2, int(round(value / 2)) * 2)
