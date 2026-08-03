from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from core.face_engine import MatchAssessment
from core.video_analysis import find_end_of_appearance, find_first_appearance


class BrightFrameEngine:
    def __init__(self, match_bright: bool = True):
        self.match_bright = match_bright
        self.frames_checked = 0

    def reference_matrix(self, _references):
        return object()

    def match_frame(self, image, _reference_matrix, _threshold):
        self.frames_checked += 1
        matched = self.match_bright and float(image.mean()) > 220
        return MatchAssessment(1 if matched else 0, 0.92 if matched else 0.0, matched)


class VideoAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="clipivo-analysis-")
        cls.video = Path(cls.temporary.name) / "late appearance.mp4"
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=black:size=160x90:rate=20:d=1",
                "-f", "lavfi", "-i", "color=c=white:size=160x90:rate=20:d=1",
                "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "20",
                str(cls.video),
            ],
            check=True,
        )
        cls.reappear_video = Path(cls.temporary.name) / "two appearances.mp4"
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=white:size=160x90:rate=20:d=1",
                "-f", "lavfi", "-i", "color=c=black:size=160x90:rate=20:d=2",
                "-f", "lavfi", "-i", "color=c=white:size=160x90:rate=20:d=1",
                "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "20",
                str(cls.reappear_video),
            ],
            check=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_find_first_appearance_decodes_sequentially_and_stops_on_match(self) -> None:
        engine = BrightFrameEngine()
        progress = []
        result = find_first_appearance(
            self.video,
            engine,
            [("reference.jpg", b"reference")],
            0.40,
            duration_ms=2000,
            sample_interval_ms=250,
            progress=lambda current, _total: progress.append(current),
        )
        self.assertIsNotNone(result)
        self.assertTrue(1000 <= result.timestamp_ms <= 1250)
        self.assertLessEqual(engine.frames_checked, 12)
        self.assertEqual(result.samples_checked, engine.frames_checked)
        self.assertTrue(progress)

    def test_find_next_can_begin_at_the_current_playhead(self) -> None:
        engine = BrightFrameEngine()
        result = find_first_appearance(
            self.video,
            engine,
            [("reference.jpg", b"reference")],
            0.40,
            duration_ms=2000,
            start_ms=1000,
            end_ms=1800,
            sample_interval_ms=500,
        )
        self.assertIsNotNone(result)
        self.assertTrue(1000 <= result.timestamp_ms <= 1250)

    def test_find_first_appearance_returns_none_when_character_is_absent(self) -> None:
        engine = BrightFrameEngine(match_bright=False)
        result = find_first_appearance(
            self.video,
            engine,
            [("reference.jpg", b"reference")],
            0.40,
            duration_ms=2000,
            sample_interval_ms=500,
        )
        self.assertIsNone(result)
        self.assertLessEqual(engine.frames_checked, 5)

    def test_keyframe_only_locator_still_refines_a_match(self) -> None:
        engine = BrightFrameEngine()
        result = find_first_appearance(
            self.video,
            engine,
            [("reference.jpg", b"reference")],
            0.40,
            duration_ms=2000,
            sample_interval_ms=1000,
            refine_interval_ms=250,
            keyframes_only=True,
        )
        self.assertIsNotNone(result)
        self.assertTrue(1000 <= result.timestamp_ms <= 1250)

    def test_next_appearance_begins_after_confirmed_absence(self) -> None:
        engine = BrightFrameEngine()
        departure = find_end_of_appearance(
            self.reappear_video,
            engine,
            [("reference.jpg", b"reference")],
            0.40,
            start_ms=0,
            end_ms=4000,
            sample_interval_ms=250,
            absence_confirm_ms=750,
        )
        self.assertIsNotNone(departure)
        self.assertTrue(1750 <= departure <= 2250)
        next_match = find_first_appearance(
            self.reappear_video,
            engine,
            [("reference.jpg", b"reference")],
            0.40,
            duration_ms=4000,
            start_ms=departure,
            end_ms=4000,
            sample_interval_ms=250,
        )
        self.assertIsNotNone(next_match)
        self.assertTrue(3000 <= next_match.timestamp_ms <= 3250)


if __name__ == "__main__":
    unittest.main()
