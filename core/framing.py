"""Output size and person framing.

Size limits apply to the shorter side, so any shape (16:9, 4:3, vertical) keeps its
proportions and nothing is ever enlarged.

Person framing works per camera shot. While a clip is tracked, the person's face is
sampled every few hundred milliseconds and camera cuts are found from frame differences.
For each shot Clipivo then finds the person's own part of the picture: the split-screen
panel they sit in (found from its border lines), with other people and static bottom
graphics such as news tickers kept out. The crop is that region, trimmed around the
person when it is much larger than they are, so its shape comes from the video rather
than from a fixed ratio. That shape decides whether the shot counts as landscape,
portrait or square. Every kept shot is scaled to one height and centred on one canvas,
so a stitched video never changes size.
"""

from __future__ import annotations

from statistics import median

MAX_SIZES = {0: "Original size", 1440: "Up to 1440p", 1080: "Up to 1080p", 720: "Up to 720p"}
FRAMINGS = {
    "whole": "Whole picture",
    "dynamic": "Follow person · dynamic",
    "landscape": "Follow person · landscape only",
    "portrait": "Follow person · portrait only",
    "square": "Follow person · square only",
}
# A crop wider than this (width / height) is landscape, narrower than the next is portrait.
_LANDSCAPE_FROM = 1.15
_PORTRAIT_BELOW = 0.87
# A region much larger than the person is trimmed to this many face widths / heights.
_MAX_WIDTH_IN_FACES = 10.0
_ABOVE_FACE = 1.5
_BELOW_FACE = 5.0
# Where the face centre sits from the top of a trimmed crop.
_FACE_LEVEL = 0.35
# Greyscale size used to find panel borders, and how many samples per shot are averaged.
BORDER_SIZE = (480, 270)
_LINE_CONTRAST = 18.0
# A panel side is a thin line or a sharp change of brightness. A side is a candidate when
# it runs along most of the face and shoulders, and a panel when all four run its length
# (overlays such as comment bubbles may cover part of one).
_STEP_CONTRAST = 24.0
_SIDE_SEED = 0.5
_SIDE_COVERAGE = 0.6
PAN_STEP_S = 1.0


def normalise_framing(value) -> str:
    value = str(value)
    return value if value in FRAMINGS else "whole"


def scale_filter(max_short_side: int) -> str:
    """Limit the shorter side, keep the shape, never enlarge."""
    n = int(max_short_side)
    return (
        f"scale='if(gte(iw,ih),-2,min(iw,{n}))':'if(gte(iw,ih),min(ih,{n}),-2)'"
        ":flags=lanczos,setsar=1"
    )


def shape_of(width: float, height: float) -> str:
    aspect = width / max(1.0, height)
    if aspect >= _LANDSCAPE_FROM:
        return "landscape"
    if aspect < _PORTRAIT_BELOW:
        return "portrait"
    return "square"


# Shots -------------------------------------------------------------------------------

def split_shots(start_ms: int, end_ms: int, cuts_ms: list[int], points: list[list[float]], describe) -> list[dict]:
    """Group face samples [t, cx, cy, fh, fw] into shots bounded by camera cuts.

    `describe(shot_start, shot_end, shot_points)` returns {"band": ..., "region": ...} for a
    shot in which the person was seen.
    """
    edges = [start_ms, *[cut for cut in sorted(cuts_ms) if start_ms < cut < end_ms], end_ms]
    shots: list[dict] = []
    for shot_start, shot_end in zip(edges, edges[1:]):
        if shot_end - shot_start < 80:
            continue
        inside = [point for point in points if shot_start <= point[0] < shot_end]
        shot = {"start": int(shot_start), "end": int(shot_end), "points": inside, "band": None, "region": None, "panel": False, "own": bool(inside)}
        if inside:
            shot.update(describe(shot_start, shot_end, inside))
        shots.append(shot)
    # A shot without the person borrows the nearest framed shot, so Whole-picture-style
    # callers never get a blank crop; person framing leaves these shots out.
    for shot in shots:
        if shot["points"]:
            continue
        neighbours = sorted(
            (other for other in shots if other["own"]),
            key=lambda other: abs(other["start"] - shot["start"]),
        )
        if neighbours:
            source = neighbours[0]
            shot["points"] = [[shot["start"], *source["points"][len(source["points"]) // 2][1:]]]
            shot["band"], shot["region"], shot["panel"] = source["band"], source["region"], source.get("panel", False)
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


def _edge_maps(picture):
    """Where a panel side could run: (vertical, horizontal) boolean maps of thin lines and
    sharp changes, in any colour channel (beige next to red is an edge even at equal
    brightness), widened by a pixel so a side can wobble by one."""
    import numpy as np

    channels = picture if picture.ndim == 3 else picture[:, :, None]
    rows, columns = channels.shape[:2]
    vertical = np.zeros((rows, columns), dtype=bool)
    horizontal = np.zeros((rows, columns), dtype=bool)
    vertical_line = np.zeros((rows, columns), dtype=bool)
    horizontal_line = np.zeros((rows, columns), dtype=bool)
    for index in range(channels.shape[2]):
        plane = channels[:, :, index]
        vertical[:, 1:-1] |= np.abs(plane[:, 2:] - plane[:, :-2]) > _STEP_CONTRAST
        horizontal[1:-1, :] |= np.abs(plane[2:, :] - plane[:-2, :]) > _STEP_CONTRAST
        for distance in (1, 2):
            centre = plane[:, distance:-distance]
            left, right = centre - plane[:, : -2 * distance], centre - plane[:, 2 * distance:]
            vertical_line[:, distance:-distance] |= (left * right > 0) & (np.minimum(np.abs(left), np.abs(right)) > _LINE_CONTRAST)
            centre = plane[distance:-distance, :]
            up, down = centre - plane[: -2 * distance, :], centre - plane[2 * distance:, :]
            horizontal_line[distance:-distance, :] |= (up * down > 0) & (np.minimum(np.abs(up), np.abs(down)) > _LINE_CONTRAST)
    for edges in (vertical, vertical_line):
        edges[:, 1:] |= edges[:, :-1].copy()
    for edges in (horizontal, horizontal_line):
        edges[1:, :] |= edges[:-1, :].copy()
    return vertical | vertical_line, horizontal | horizontal_line, vertical_line, horizontal_line


def _find_panel(
    picture, face: tuple[float, float, float, float], others: list[tuple[float, float, float, float]] = (),
) -> tuple[int, int, int, int] | None:
    """The window or split-screen panel the person is in: a rectangle around their face and
    shoulders whose four sides are straight edges or the picture's own edge, and which holds
    nobody else's face. Clearer sides win, then the tighter rectangle. Coordinates are in
    `picture` pixels; None when the person's panel is the whole picture."""
    import numpy as np

    rows, columns = picture.shape[:2]
    vertical, horizontal, _vertical_line, _horizontal_line = _edge_maps(picture)
    x1, y1, x2, y2 = face
    face_w, face_h = x2 - x1, y2 - y1
    r0, r1 = max(0, int(y1 - 0.2 * face_h)), min(rows, int(y2 + 0.6 * face_h) + 1)
    c0, c1 = max(0, int(x1 - 0.5 * face_w)), min(columns, int(x2 + 0.5 * face_w) + 1)
    column_cover = vertical[r0:r1].mean(axis=0)
    row_cover = horizontal[:, c0:c1].mean(axis=1)

    def nearest(candidates, key):
        # A thick edge (an overlay's border, a soft shadow) marks several neighbouring
        # rows; keep the two ends of each run so the real side beyond it is still tried.
        ordered = sorted(set(candidates))
        ends = [value for index, value in enumerate(ordered)
                if index == 0 or index == len(ordered) - 1
                or ordered[index - 1] != value - 1 or ordered[index + 1] != value + 1]
        return sorted(ends, key=key)[:10]

    candidates = np.flatnonzero(column_cover >= _SIDE_SEED)
    lefts = nearest([0, *[int(x) for x in candidates if x < x1 - 0.2 * face_w]], lambda x: -x)
    rights = nearest([columns, *[int(x) + 1 for x in candidates if x > x2 + 0.2 * face_w]], lambda x: x)
    candidates = np.flatnonzero(row_cover >= _SIDE_SEED)
    tops = nearest([0, *[int(y) for y in candidates if y < y1 - 0.1 * face_h]], lambda y: -y)
    bottoms = nearest([rows, *[int(y) + 1 for y in candidates if y > y2 + 0.5 * face_h]], lambda y: y)

    def side(edges, fixed, low, high, at_border):
        if at_border:
            return 1.0
        return float(edges[low:high, fixed].mean()) if edges is vertical else float(edges[fixed, low:high].mean())

    rivals = [box for box in others if box[3] - box[1] >= 0.5 * face_h]
    best = None
    for left in lefts:
        for right in rights:
            if right - left < 1.8 * face_w:
                continue
            for top in tops:
                for bottom in bottoms:
                    if bottom - top < 1.6 * face_h:
                        continue
                    if any(_inside_share(rival, (left, top, right, bottom)) >= 0.3 for rival in rivals):
                        continue
                    drawn = [
                        side(edges, fixed, low, high, False)
                        for edges, fixed, low, high, at_border in (
                            (vertical, left, top, bottom, left == 0),
                            (vertical, min(columns - 1, right - 1), top, bottom, right == columns),
                            (horizontal, top, left, right, top == 0),
                            (horizontal, min(rows - 1, bottom - 1), left, right, bottom == rows),
                        )
                        if not at_border
                    ]
                    if not drawn or min(drawn) < _SIDE_COVERAGE:
                        continue
                    # Clearer sides first, judged on the sides the rectangle draws itself (the
                    # picture's own edges say nothing). An edge inside the person's picture,
                    # such as a wall corner behind them, is partly hidden by them and so less
                    # clear than a real border. Between equally clear ones the tighter wins.
                    rank = (round(sum(drawn) / len(drawn) * 20), -(right - left) * (bottom - top))
                    if best is None or rank > best[0]:
                        best = (rank, (left, top, right, bottom))
    if best is None or best[1] == (0, 0, columns, rows):
        return None
    return best[1]


def _inside_share(box, rectangle) -> float:
    """How much of a face box lies inside a rectangle."""
    width = min(box[2], rectangle[2]) - max(box[0], rectangle[0])
    height = min(box[3], rectangle[3]) - max(box[1], rectangle[1])
    area = max(1e-6, (box[2] - box[0]) * (box[3] - box[1]))
    return max(0.0, width) * max(0.0, height) / area


def _steady_faces(samples: list[list[tuple]], face_w: float) -> list[tuple]:
    """Other faces that stay in one place through a shot: seen in at least a fifth of its
    samples. One stray detection must not decide where a panel ends."""
    groups: list[list[tuple]] = []
    for boxes in samples:
        for box in boxes:
            centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
            for group in groups:
                first = group[0]
                if abs((first[0] + first[2]) / 2 - centre[0]) < face_w / 2 and abs((first[1] + first[3]) / 2 - centre[1]) < face_w / 2:
                    group.append(box)
                    break
            else:
                groups.append([box])
    needed = max(1, len(samples) / 5)
    return [
        tuple(median(box[index] for box in group) for index in range(4))
        for group in groups if len(group) >= needed
    ]


def person_region(
    border_frames: list,
    band_frames: list,
    faces: list[tuple[float, float, float, float]],
    others: list[list[tuple[float, float, float, float]]],
    frame_size: tuple[int, int],
) -> dict:
    """The person's own part of the picture in one shot: {"region": [left, top, right,
    bottom], "band": ..., "panel": bool}.

    `faces` are the person's face boxes, `others` every other face per sample, both in
    frame pixels. Panels are looked for in `border_frames` (colour, BORDER_SIZE). With a
    panel the region is that panel; without one it is the picture, divided between the
    person and anyone else of a similar size.
    """
    import numpy as np

    frame_w, frame_h = frame_size
    x1, y1, x2, y2 = (median(box[index] for box in faces) for index in range(4))
    face_w, face_h = x2 - x1, y2 - y1
    left, top, right, bottom = 0.0, 0.0, float(frame_w), float(frame_h)
    panel = None
    if border_frames:
        picture = np.median(np.stack([frame.astype(np.float32) for frame in border_frames]), axis=0)
        rows, columns = picture.shape[:2]
        sx, sy = columns / frame_w, rows / frame_h
        rivals = [(ox1 * sx, oy1 * sy, ox2 * sx, oy2 * sy) for ox1, oy1, ox2, oy2 in _steady_faces(others, face_w)]
        panel = _find_panel(picture, (x1 * sx, y1 * sy, x2 * sx, y2 * sy), rivals)
        if panel:
            left, top, right, bottom = panel[0] / sx, panel[1] / sy, panel[2] / sx, panel[3] / sy

    # Other people at a similar size inside the region: split the space halfway between
    # the faces, beside or above and below.
    centre_x, centre_y = (x1 + x2) / 2, (y1 + y2) / 2
    splits = {"left": [], "right": [], "top": [], "bottom": []}
    for boxes in others:
        sample = {"left": left, "right": right, "top": top, "bottom": bottom}
        for ox1, oy1, ox2, oy2 in boxes:
            if oy2 - oy1 < 0.6 * face_h or not (left <= (ox1 + ox2) / 2 <= right and top <= (oy1 + oy2) / 2 <= bottom):
                continue
            beside = oy2 > y1 - face_h and oy1 < y2 + face_h
            if beside and (ox1 + ox2) / 2 < centre_x:
                sample["left"] = max(sample["left"], (ox2 + x1) / 2)
            elif beside:
                sample["right"] = min(sample["right"], (ox1 + x2) / 2)
            elif (oy1 + oy2) / 2 < centre_y:
                # Someone above: their body runs down under their face.
                sample["top"] = max(sample["top"], oy2 + 0.6 * (y1 - oy2))
            else:
                sample["bottom"] = min(sample["bottom"], (y2 + oy1) / 2)
        for key in splits:
            splits[key].append(sample[key])
    if others:
        left, right = max(left, median(splits["left"])), min(right, median(splits["right"]))
        top, bottom = max(top, median(splits["top"])), min(bottom, median(splits["bottom"]))

    # Static graphics at the bottom, judged across the whole width: inside one panel a
    # person sitting still would look like a graphic.
    band = None
    if band_frames and not panel:
        # A found panel already ends at its own edge; a still band inside it is the person
        # sitting still or a small caption, not a ticker to keep out.
        band = find_static_band(band_frames, frame_h)
        if band is not None and y2 + 0.6 * face_h < band < bottom:
            bottom = band
        else:
            band = None
    if right - left < face_w * 1.2 or bottom - top < face_h * 1.6:
        left, right = max(0.0, x1 - 2 * face_w), min(float(frame_w), x2 + 2 * face_w)
        top, bottom = max(0.0, y1 - face_h), min(float(frame_h), y2 + 3 * face_h)
        panel = None
    return {"region": [round(left, 1), round(top, 1), round(right, 1), round(bottom, 1)], "band": band, "panel": bool(panel)}


# Crops -------------------------------------------------------------------------------

def shot_crop(shot: dict, frame_size: tuple[int, int]) -> tuple[int, int, list[tuple[float, int]], int]:
    """(width, height, [(seconds from shot start, x)], y) for one shot."""
    frame_w, frame_h = frame_size
    points = shot.get("points") or []
    region = shot.get("region") or [0, 0, frame_w, frame_h]
    left, top, right, bottom = (float(value) for value in region)
    if not points:
        return _even(right - left), _even(bottom - top), [(0.0, _even(left))], _even(top)
    if shot.get("panel"):
        # The person's own window or panel: use all of it.
        width, height = min(_even(right - left), frame_w - frame_w % 2), min(_even(bottom - top), frame_h - frame_h % 2)
        return width, height, [(0.0, _even(min(left, frame_w - width)))], _even(min(top, frame_h - height))
    face_w = median(point[4] if len(point) > 4 else point[3] * 0.75 for point in points)
    face_h = median(point[3] for point in points)
    centre_y = median(point[2] for point in points)
    width = min(right - left, face_w * _MAX_WIDTH_IN_FACES)
    crop_top = max(top, centre_y - face_h / 2 - face_h * _ABOVE_FACE)
    crop_bottom = min(bottom, centre_y + face_h / 2 + face_h * _BELOW_FACE)
    height = crop_bottom - crop_top
    width, height = min(_even(width), frame_w - frame_w % 2), min(_even(height), frame_h - frame_h % 2)
    y = min(max(top, crop_top), bottom - height)
    keys = _pan_keys(shot, points, width, left, right)
    return width, height, keys, _even(max(0.0, min(y, frame_h - height)))


def _pan_keys(shot: dict, points: list[list[float]], width: int, left: float, right: float) -> list[tuple[float, int]]:
    start = shot["start"]
    lowest, highest = left, max(left, right - width)
    xs = [min(max(lowest, point[1] - width / 2), highest) for point in points]
    times = [(point[0] - start) / 1000 for point in points]
    if max(xs) - min(xs) < (right - left) * 0.04:
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
            keys.append((max(0.0, t), _even(min(max(lowest, x), highest))))
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


def framed_shots(shots: list[dict], framing: str, frame_size: tuple[int, int]) -> list[tuple[dict, tuple]]:
    """The shots a framing keeps, each with its crop: shots where the person was seen, and
    for landscape, portrait or square only those whose crop has that shape."""
    kept = []
    for shot in shots:
        if not shot.get("own", True) or not shot.get("points"):
            continue
        crop = shot_crop(shot, frame_size)
        if framing != "dynamic" and shape_of(crop[0], crop[1]) != framing:
            continue
        kept.append((shot, crop))
    return kept


def canvas_size(crops: list[tuple], max_short_side: int) -> tuple[tuple[int, int], float]:
    """(canvas, scale) for crops given as (width, height[, ...]).

    Every crop keeps its own size: nothing is enlarged, so the person looks exactly as big
    as in the source. The canvas is just big enough for the widest and the tallest crop.
    With a Size limit everything shrinks by the same scale, so views keep their sizes
    relative to each other.
    """
    width = float(max(crop[0] for crop in crops))
    height = float(max(crop[1] for crop in crops))
    scale = 1.0
    if max_short_side and min(width, height) > max_short_side:
        scale = max_short_side / min(width, height)
    return (_even(width * scale), _even(height * scale)), scale


def fit_filter(width: int, height: int, canvas: tuple[int, int], scale: float = 1.0) -> str:
    """Show a crop at its own size (times `scale`), centred on the canvas with black around it."""
    canvas_w, canvas_h = canvas
    out_w, out_h = min(canvas_w, _even(width * scale)), min(canvas_h, _even(height * scale))
    if (out_w, out_h) == (width, height):
        resize = "setsar=1"
    else:
        resize = f"scale={out_w}:{out_h}:flags=lanczos,setsar=1"
    return f"{resize},pad={canvas_w}:{canvas_h}:{(canvas_w - out_w) // 2}:{(canvas_h - out_h) // 2}:black"


def _even(value: float) -> int:
    return max(2, int(round(value / 2)) * 2)
