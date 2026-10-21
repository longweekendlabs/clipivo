"""Output size and person framing.

Size limits apply to the shorter side, so any shape (16:9, 4:3, vertical) keeps its
proportions and nothing is ever enlarged.

Person framing works per camera shot. While a clip is tracked, the person's face is
sampled every few hundred milliseconds and camera cuts are found from frame differences.
Each shot gets its own crop: sized from the face, the face kept at the same height in the
picture, panning smoothly if the person moves, and kept above static bottom graphics such
as news tickers when that still leaves head and shoulders in view. Every shot is scaled to
one output size, so a stitched video never changes shape.
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
# Crop height in face heights and where the face centre sits from the top of the crop.
_HEIGHT_IN_FACES = {"landscape": 2.8, "portrait": 4.2, "square": 3.0}
_FACE_LEVEL = 0.38
_MIN_HEIGHT_IN_FACES = 2.2
DEFAULT_CROP_SHORT_SIDE = 1080
PAN_STEP_S = 1.0


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


# Shots -------------------------------------------------------------------------------

def split_shots(start_ms: int, end_ms: int, cuts_ms: list[int], points: list[list[float]], bands) -> list[dict]:
    """Group face samples [t, cx, cy, fh] into shots bounded by camera cuts.

    `bands(shot_start, shot_end)` returns the top of a static bottom band in pixels, or None.
    """
    edges = [start_ms, *[cut for cut in sorted(cuts_ms) if start_ms < cut < end_ms], end_ms]
    shots: list[dict] = []
    for shot_start, shot_end in zip(edges, edges[1:]):
        if shot_end - shot_start < 80:
            continue
        inside = [point for point in points if shot_start <= point[0] < shot_end]
        shots.append({"start": int(shot_start), "end": int(shot_end), "points": inside, "band": bands(shot_start, shot_end)})
    # A shot without a face sample borrows the nearest framed shot so the crop never goes blank.
    for index, shot in enumerate(shots):
        if shot["points"]:
            continue
        neighbours = sorted(
            (other for other in shots if other["points"]),
            key=lambda other: abs(other["start"] - shot["start"]),
        )
        if neighbours:
            shot["points"] = [[shot["start"], *neighbours[0]["points"][len(neighbours[0]["points"]) // 2][1:]]]
            shot["band"] = shot["band"] if shot["band"] is not None else neighbours[0]["band"]
    return shots


def find_static_band(small_frames: list, frame_height: int) -> float | None:
    """Top of a band at the bottom of the picture that stays still across the shot: tickers,
    headline boxes, social bars. Returns its top in full-frame pixels, or None."""
    if len(small_frames) < 4:
        return None
    import numpy as np

    stack = np.stack([frame.astype(np.float32) for frame in small_frames])
    # Still most of the time, so a headline that changes once mid-shot still counts.
    still = (np.abs(np.diff(stack, axis=0)) < 4.0).mean(axis=0) >= 0.85
    still_rows = still.mean(axis=1) >= 0.9
    rows = len(still_rows)
    allowed_gap = max(1, round(rows * 0.05))  # a thin scrolling crawl inside the band
    top = None
    gap = 0
    for row in range(rows - 1, -1, -1):
        if still_rows[row]:
            top = row
            gap = 0
        elif top is not None:
            gap += 1
            if gap > allowed_gap:
                break
        elif rows - row > allowed_gap:
            return None  # the bottom edge itself moves: no band
    if top is None:
        return None
    band_height = (rows - top) / rows
    if band_height < 0.06 or top / rows < 0.45:
        return None
    # News graphics usually carry a scrolling crawl right on top of the still band; keep clear of it.
    return max(0.0, (top / rows - 0.04)) * frame_height


# Crops -------------------------------------------------------------------------------

def shot_crop(shot: dict, framing: str, frame_size: tuple[int, int]) -> tuple[int, int, list[tuple[float, int]], int]:
    """(width, height, [(seconds from shot start, x)], y) for one shot."""
    frame_w, frame_h = frame_size
    aspect = _ASPECT[framing]
    points = shot.get("points") or []
    if not points:
        height = min(frame_h, frame_w / aspect)
        width, height = _fit(height * aspect, height, frame_w, frame_h, aspect)
        return width, height, [(0.0, _even((frame_w - width) / 2))], _even((frame_h - height) / 2)
    face_h = median(point[3] for point in points)
    centre_y = median(point[2] for point in points)
    height = face_h * _HEIGHT_IN_FACES[framing]
    band = shot.get("band")
    if band is not None and band > centre_y + face_h * 0.6:
        # Keep the crop above the ticker if a head-and-shoulders framing still fits.
        room = (band - centre_y) / (1 - _FACE_LEVEL)
        if room >= face_h * _MIN_HEIGHT_IN_FACES:
            height = min(height, room)
    if band is not None:
        # A face high in the picture pins the crop to the top edge; the bottom limit still holds.
        top = max(0.0, centre_y - height * _FACE_LEVEL)
        if top + height > band and band - top >= face_h * _MIN_HEIGHT_IN_FACES:
            height = band - top
    width, height = _fit(height * aspect, height, frame_w, frame_h, aspect)
    top = min(max(0.0, centre_y - height * _FACE_LEVEL), frame_h - height)
    keys = _pan_keys(shot, points, width, frame_w)
    return width, height, keys, _even(min(top, frame_h - height))


def _pan_keys(shot: dict, points: list[list[float]], width: int, frame_w: int) -> list[tuple[float, int]]:
    start = shot["start"]
    xs = [min(max(0.0, point[1] - width / 2), frame_w - width) for point in points]
    times = [(point[0] - start) / 1000 for point in points]
    if max(xs) - min(xs) < frame_w * 0.04:
        return [(0.0, _even(median(xs)))]
    # Smooth over about two seconds, then keep one key per second so the pan glides.
    smoothed = []
    for index in range(len(xs)):
        window = [x for t, x in zip(times, xs) if abs(t - times[index]) <= 1.0]
        smoothed.append(sum(window) / len(window))
    keys: list[tuple[float, int]] = []
    next_key = -1.0
    for t, x in zip(times, smoothed):
        if t >= next_key:
            keys.append((max(0.0, t), _even(min(max(0.0, x), frame_w - width))))
            next_key = t + PAN_STEP_S
    return keys


def crop_filter(width: int, height: int, keys: list[tuple[float, int]], y: int) -> str:
    if len(keys) == 1:
        return f"crop={width}:{height}:{keys[0][1]}:{y}"
    expression = str(keys[-1][1])
    for (t0, x0), (t1, x1) in reversed(list(zip(keys, keys[1:]))):
        span = max(0.001, t1 - t0)
        expression = f"if(lt(t,{t1:.3f}),{x0}+({x1}-{x0})*(t-{t0:.3f})/{span:.3f},{expression})"
    expression = f"if(lt(t,{keys[0][0]:.3f}),{keys[0][1]},{expression})"
    return f"crop={width}:{height}:x='{expression}':y={y}"


def _fit(width: float, height: float, frame_w: int, frame_h: int, aspect: float) -> tuple[int, int]:
    if height > frame_h:
        height = frame_h
        width = height * aspect
    if width > frame_w:
        width = frame_w
        height = width / aspect
    return min(_even(width), frame_w - frame_w % 2), min(_even(height), frame_h - frame_h % 2)


def _even(value: float) -> int:
    return max(2, int(round(value / 2)) * 2)
