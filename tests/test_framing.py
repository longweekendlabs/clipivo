from __future__ import annotations

import unittest
from types import SimpleNamespace

from core.export import OutputShape
from core.framing import crop_box, output_size, subject_from_hits
from core.models import ClipRange


class FramingTests(unittest.TestCase):
    def test_size_limit_keeps_shape_for_landscape_portrait_and_four_three(self) -> None:
        shape = OutputShape(95, 1080, "whole", (3840, 2160))
        self.assertTrue(shape.needs_reencode())
        _before, after = shape.chain_for(ClipRange(0, 1000))
        self.assertIn("min(ih,1080)", after)
        self.assertIn("min(iw,1080)", after)
        self.assertFalse(OutputShape(100, 1080, "whole", (1920, 1080)).needs_reencode())
        self.assertTrue(OutputShape(90, 0, "whole", (1920, 1080)).needs_reencode())

    def test_output_sizes(self) -> None:
        self.assertEqual(output_size("landscape", 1080), (1920, 1080))
        self.assertEqual(output_size("portrait", 1080), (1080, 1920))
        self.assertEqual(output_size("square", 720), (720, 720))
        self.assertEqual(output_size("portrait", 0), (1080, 1920))

    def test_portrait_crop_stays_on_the_person_in_a_split_screen(self) -> None:
        hits = [
            SimpleNamespace(box=(420, 250, 600, 470), frame_size=(1920, 1080)),
            SimpleNamespace(box=(430, 255, 610, 475), frame_size=(1920, 1080)),
        ]
        subject = subject_from_hits(hits)
        x, y, w, h = crop_box(subject, "portrait", (1920, 1080))
        self.assertLessEqual(x + w, 1920)
        self.assertLessEqual(y + h, 1080)
        self.assertLess(x, 515)
        self.assertGreater(x + w, 515)
        self.assertLessEqual(x + w, 960, "crop must stay in the left half of the split screen")
        self.assertAlmostEqual(w / h, 9 / 16, delta=0.02)

    def test_crop_without_face_data_is_centred_and_inside_the_frame(self) -> None:
        x, y, w, h = crop_box(None, "portrait", (1920, 1080))
        self.assertEqual((y, h), (0, 1080))
        self.assertEqual(x, (1920 - w) // 2 // 2 * 2)
        shape = OutputShape(95, 1080, "portrait", (1920, 1080))
        before, after = shape.chain_for(ClipRange(0, 1000))
        self.assertTrue(before.startswith("crop="))
        self.assertIn("scale=1080:1920", after)


if __name__ == "__main__":
    unittest.main()
