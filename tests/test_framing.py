from __future__ import annotations

import unittest

import numpy as np

from core.export import OutputShape, _with_framed_size
from core.framing import crop_filter, find_static_band, output_size, shot_crop, split_shots
from core.models import ClipRange


def face(t, cx, cy=320, fh=170):
    return [t, cx, cy, fh]


class FramingTests(unittest.TestCase):
    def test_size_limit_keeps_shape_and_never_enlarges(self) -> None:
        shape = OutputShape(95, 1080, "whole", (3840, 2160))
        self.assertTrue(shape.needs_reencode())
        (_clip, (_before, after)), = shape.pieces(ClipRange(0, 1000))
        self.assertIn("min(ih,1080)", after)
        self.assertFalse(OutputShape(100, 1080, "whole", (1920, 1080)).needs_reencode())
        self.assertEqual(output_size("portrait", 1080), (1080, 1920))
        self.assertEqual(output_size("square", 720), (720, 720))

    def test_each_camera_shot_gets_its_own_crop(self) -> None:
        # Split screen with her on the right half, then a full-screen shot of her.
        points = [face(t, 1450) for t in range(0, 4000, 400)] + [face(t, 960, 380, 330) for t in range(4000, 8000, 400)]
        shots = split_shots(0, 8000, [4000], points, lambda _a, _b: None)
        self.assertEqual([(shot["start"], shot["end"]) for shot in shots], [(0, 4000), (4000, 8000)])
        first = shot_crop(shots[0], "portrait", (1920, 1080))
        second = shot_crop(shots[1], "portrait", (1920, 1080))
        self.assertGreater(first[2][0][1], 960, "first shot stays on the right half")
        self.assertLess(abs(second[2][0][1] + second[0] / 2 - 960), 4, "second shot is centred on her")

    def test_crop_stays_above_a_static_ticker(self) -> None:
        shot = {"start": 0, "end": 4000, "points": [face(t, 960, 300, 300) for t in range(0, 4000, 400)], "band": 840.0}
        width, height, _keys, y = shot_crop(shot, "portrait", (1920, 1080))
        self.assertLessEqual(y + height, 842)
        self.assertGreaterEqual(height, 300 * 2.2)

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
        shot = {"start": 0, "end": 6000, "points": [face(t, 500 + t / 10) for t in range(0, 6000, 400)], "band": None}
        width, height, keys, y = shot_crop(shot, "portrait", (1920, 1080))
        self.assertGreater(len(keys), 2)
        self.assertIn("x='if(lt(t,", crop_filter(width, height, keys, y))

    def test_framed_output_uses_one_size_from_the_typical_crop(self) -> None:
        clip = ClipRange(0, 4000, shots=[{"start": 0, "end": 4000, "points": [face(t, 960) for t in range(0, 4000, 400)], "band": None}], frame=[1920, 1080])
        shape = _with_framed_size(OutputShape(95, 1080, "portrait", (1920, 1080)), [clip])
        self.assertGreater(shape.out_short, 0)
        self.assertLessEqual(shape.out_short, 1080)
        (_piece, (before, after)), = shape.pieces(clip)
        self.assertTrue(before.startswith("crop="))
        self.assertIn(f"scale={shape.out_short}:", after)


if __name__ == "__main__":
    unittest.main()
