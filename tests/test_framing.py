from __future__ import annotations

import unittest

import numpy as np

from core.export import OutputShape, _with_framed_size, framed_durations
from core.batch import BatchConfiguration, untracked_clips
from core.framing import (
    FRAMINGS, canvas_size, crop_filter, fit_filter, find_static_band, framed_shots, normalise_framing, person_region,
    shape_of, shot_crop, split_shots,
)
from core.models import ClipRange


def face(t, cx, cy=320, fh=170, fw=130):
    return [t, cx, cy, fh, fw]


def box(cx, cy=320, fh=170, fw=130):
    return (cx - fw / 2, cy - fh / 2, cx + fw / 2, cy + fh / 2)


def shot(start, end, region, cx, cy=320, fh=170, fw=130, own=True):
    return {
        "start": start, "end": end, "band": None, "region": region, "own": own,
        "points": [face(t, cx, cy, fh, fw) for t in range(start, end, 400)],
    }


def split_screen(panel_left=1350, panel_right=1770, panel_top=110, panel_bottom=815):
    """A 480x270 colour frame with a bright one-pixel border around the right panel."""
    rng = np.random.default_rng(3)
    picture = np.repeat(rng.integers(80, 95, (270, 480, 1)), 3, axis=2).astype(np.uint8)
    sx, sy = 480 / 1920, 270 / 1080
    left, right = int(panel_left * sx), int(panel_right * sx)
    top, bottom = int(panel_top * sy), int(panel_bottom * sy)
    picture[top:bottom, left] = 250
    picture[top:bottom, right] = 250
    picture[top, left:right] = 250
    picture[bottom, left:right] = 250
    return picture


def floating_window(left=980, top=290, right=1880, bottom=795):
    """A webcam window floating on a flat background; it differs in colour, not brightness."""
    picture = np.zeros((270, 480, 3), dtype=np.uint8)
    picture[:] = (90, 80, 40)  # dark teal background (BGR)
    l, t, r, b = left // 4, top // 4, right // 4, bottom // 4
    rng = np.random.default_rng(5)
    picture[t:b, l:r] = (40, 80, 150) + rng.integers(-6, 6, (b - t, r - l, 3))  # warm room
    return picture


class FramingTests(unittest.TestCase):
    def test_size_limit_keeps_shape_and_never_enlarges(self) -> None:
        shape = OutputShape(95, 1080, "whole", (3840, 2160))
        self.assertTrue(shape.needs_reencode())
        (_clip, (_before, after)), = shape.pieces(ClipRange(0, 1000))
        self.assertIn("min(ih,1080)", after)
        self.assertFalse(OutputShape(100, 1080, "whole", (1920, 1080)).needs_reencode())

    def test_split_screen_panel_becomes_the_region(self) -> None:
        frames = [split_screen() for _ in range(3)]
        faces = [box(1560, 340, 200, 140)] * 3
        found = person_region(frames, [], faces, [[] for _ in faces], (1920, 1080))
        self.assertTrue(found["panel"])
        left, top, right, bottom = found["region"]
        self.assertLess(abs(left - 1354), 10)
        self.assertLess(abs(right - 1770), 10)
        self.assertLess(abs(top - 112), 10)
        self.assertLess(abs(bottom - 815), 10)
        self.assertEqual(shape_of(right - left, bottom - top), "portrait")

    def test_one_stray_face_detection_does_not_shrink_the_panel(self) -> None:
        faces = [box(1560, 340, 200, 140)] * 10
        others = [[] for _ in range(9)] + [[box(1700, 500, 180, 130)]]
        found = person_region([split_screen()] * 3, [], faces, others, (1920, 1080))
        self.assertTrue(found["panel"])
        self.assertLess(abs(found["region"][2] - 1770), 10)

    def test_floating_window_is_found_by_its_colour(self) -> None:
        found = person_region([floating_window()] * 3, [], [box(1420, 480, 230, 165)] * 3, [[]] * 3, (1920, 1080))
        self.assertTrue(found["panel"])
        for value, expected in zip(found["region"], [980, 290, 1880, 795]):
            self.assertLess(abs(value - expected), 8)

    def test_without_a_panel_people_above_and_beside_are_kept_out(self) -> None:
        above = box(1400, 150)
        beside = box(500, 600)
        found = person_region([], [], [box(1400, 600)] * 3, [[above, beside]] * 3, (1920, 1080))
        left, top, _right, _bottom = found["region"]
        self.assertFalse(found["panel"])
        self.assertGreater(left, 900)
        self.assertGreater(top, 235)

    def test_other_people_are_kept_out(self) -> None:
        faces = [box(1400)] * 3
        others = [[box(700)]] * 3
        region = person_region([], [], faces, others, (1920, 1080))["region"]
        self.assertGreater(region[0], 900, "the person on the left is left out")
        self.assertEqual(region[2], 1920)

    def test_crop_follows_the_region_shape(self) -> None:
        panel = shot(0, 4000, [1350, 110, 1770, 815], 1560, 340, 200, 140)
        panel["panel"] = True
        width, height, keys, y = shot_crop(panel, (1920, 1080))
        self.assertEqual((width, height, keys[0][1], y), (420, 704, 1350, 110))
        full = shot(0, 4000, [0, 0, 1920, 830], 700, 300, 260, 190)
        width, height, _keys, _y = shot_crop(full, (1920, 1080))
        self.assertEqual(shape_of(width, height), "landscape")

    def test_shapes_pick_their_shots_and_dynamic_keeps_all(self) -> None:
        shots = [
            shot(0, 4000, [1350, 110, 1770, 815], 1560, 340, 200, 140),
            shot(4000, 8000, [0, 0, 1920, 830], 700, 300, 260, 190),
            shot(8000, 9000, [0, 0, 1920, 830], 700, own=False),
        ]
        kept = lambda framing: [item["start"] for item, _crop in framed_shots(shots, framing, (1920, 1080))]
        self.assertEqual(kept("dynamic"), [0, 4000])
        self.assertEqual(kept("portrait"), [0])
        self.assertEqual(kept("landscape"), [4000])
        self.assertEqual(kept("square"), [])

    def test_stitched_shots_share_one_centred_canvas(self) -> None:
        clip = ClipRange(0, 10000, frame=[1920, 1080], shots=[
            shot(0, 4000, [1350, 110, 1770, 815], 1560, 340, 200, 140),
            shot(4000, 10000, [0, 0, 1920, 830], 700, 300, 260, 190),
        ])
        shape = _with_framed_size(OutputShape(95, 0, "dynamic", (1920, 1080)), [clip])
        canvas_w, canvas_h = shape.canvas
        self.assertGreater(canvas_w, canvas_h)
        pieces = shape.pieces(clip)
        self.assertEqual(len(pieces), 2)
        for _piece, (before, after) in pieces:
            self.assertTrue(before.startswith("crop="))
            self.assertIn(f"pad={canvas_w}:{canvas_h}:", after)
        self.assertEqual(framed_durations(OutputShape(95, 0, "landscape", (1920, 1080)), [clip]), [6000])
        # The canvas takes the shape seen longest; Size limits its shorter side.
        # Nothing is enlarged: the canvas holds the widest and tallest crop at their own size;
        # Size shrinks everything by one scale.
        self.assertEqual(canvas_size([(948, 1080, 300), (540, 404, 200), (936, 504, 9)], 0), ((948, 1080), 1.0))
        (canvas, scale) = canvas_size([(1920, 880), (388, 324)], 720)
        self.assertEqual(canvas, (1570, 720))
        self.assertIn("scale=318:266", fit_filter(388, 324, canvas, scale))
        self.assertEqual(fit_filter(388, 324, (1920, 880)), "setsar=1,pad=1920:880:766:278:black")

    def test_sub_second_layout_changes_are_left_out(self) -> None:
        clip = ClipRange(0, 9000, frame=[1920, 1080], shots=[
            shot(0, 4000, [1350, 110, 1770, 815], 1560, 340, 200, 140),
            shot(4000, 4700, [0, 0, 1920, 830], 700, 300, 260, 190),
            shot(4700, 9000, [0, 500, 560, 950], 280, 700, 200, 140),
        ])
        for item in clip.shots:
            item["panel"] = item["region"][1] > 0
        shape = _with_framed_size(OutputShape(95, 0, "dynamic", (1920, 1080)), [clip])
        self.assertEqual([(piece.start_ms, piece.end_ms) for piece, _chain in shape.pieces(clip)], [(0, 4000), (4700, 9000)])

    def test_cuts_in_another_panel_keep_one_piece_for_the_person(self) -> None:
        # The middle panel cuts twice; her panel stays, with a shorter crop judged in a short shot.
        long_shot = shot(0, 10000, [1350, 110, 1770, 815], 1560, 340, 200, 140)
        short_shot = shot(10000, 11700, [1350, 110, 1770, 700], 1560, 340, 200, 140)
        later = shot(11700, 20000, [1352, 112, 1772, 815], 1560, 340, 200, 140)
        clip = ClipRange(0, 20000, frame=[1920, 1080], shots=[long_shot, short_shot, later])
        shape = _with_framed_size(OutputShape(95, 0, "portrait", (1920, 1080)), [clip])
        (piece, (before, after)), = shape.pieces(clip)
        self.assertEqual((piece.start_ms, piece.end_ms), (0, 20000))
        self.assertEqual(before, "crop=420:704:1350:110")
        self.assertIn("pad=420:704:0:0", after)

    def test_split_shots_marks_shots_without_the_person(self) -> None:
        points = [face(t, 1450) for t in range(0, 4000, 400)]
        shots = split_shots(0, 8000, [4000], points, lambda _a, _b, _p: {"region": [0, 0, 1920, 1080], "band": None})
        self.assertEqual([(item["start"], item["own"]) for item in shots], [(0, True), (4000, False)])

    def test_static_band_found_at_the_bottom(self) -> None:
        rng = np.random.default_rng(1)
        frames = []
        for _ in range(6):
            frame = rng.integers(0, 255, (90, 160)).astype(np.uint8)
            frame[72:] = 40  # a still ticker in the bottom fifth
            frames.append(frame)
        band = find_static_band(frames, 1080)
        self.assertIsNotNone(band)
        self.assertLess(abs(band - (72 / 90 - 0.04) * 1080), 15)
        self.assertIsNone(find_static_band([rng.integers(0, 255, (90, 160)).astype(np.uint8) for _ in range(6)], 1080))

    def test_moving_person_gets_a_smooth_pan(self) -> None:
        moving = shot(0, 6000, [0, 0, 1920, 1080], 500, 320, 100, 80)
        moving["points"] = [face(t, 500 + t / 10, 320, 100, 80) for t in range(0, 6000, 400)]
        width, height, keys, y = shot_crop(moving, (1920, 1080))
        self.assertGreater(len(keys), 2)
        self.assertIn("x='if(lt(t,", crop_filter(width, height, keys, y))

    def test_framing_choices(self) -> None:
        self.assertEqual(list(FRAMINGS), ["whole", "dynamic", "landscape", "portrait", "square"])
        self.assertEqual(normalise_framing("nonsense"), "whole")
        self.assertEqual(BatchConfiguration.from_dict({"framing": "square"}).framing, "square")

    def test_clips_without_tracking_or_with_moved_edges_need_tracking(self) -> None:
        shots = [shot(0, 4000, [0, 0, 1920, 1080], 960)]
        tracked = ClipRange(0, 4000, shots=shots, frame=[1920, 1080])
        reopened = ClipRange(0, 4000)
        extended = ClipRange(0, 6000, shots=shots, frame=[1920, 1080])
        trimmed = ClipRange(500, 3500, shots=shots, frame=[1920, 1080])
        before_regions = ClipRange(0, 4000, shots=[{"start": 0, "end": 4000, "points": [], "band": None}], frame=[1920, 1080])
        self.assertEqual(
            untracked_clips([tracked, reopened, extended, trimmed, before_regions]),
            [reopened, extended, before_regions],
        )


if __name__ == "__main__":
    unittest.main()
