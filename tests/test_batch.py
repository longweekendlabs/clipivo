from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.batch import (
    AppearanceHit,
    BatchCharacterTarget,
    BatchConfiguration,
    BatchVideoEntry,
    analyse_video_for_characters,
    build_appearance_ranges,
    discover_videos,
    load_batch_configuration,
    move_source_to_trash,
    safe_name_component,
    save_batch_configuration,
    validate_export,
)
from core.face_engine import MatchAssessment


class MultiTargetEngine:
    def __init__(self):
        self.frames_checked = 0
        self.shared_frames_checked = 0

    def reference_matrix(self, references):
        return references[0][1]

    def match_frame_many(self, image, targets):
        self.frames_checked += 1
        if len(targets) > 1:
            self.shared_frames_checked += 1
        bright = float(image.mean()) > 100
        return {
            target_id: MatchAssessment(1, 0.9 if matched else 0.1, matched)
            for target_id, (matrix, _threshold) in targets.items()
            for matched in [bright if matrix == b"bright" else not bright]
        }


class BatchCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="clipivo-batch-tests-")
        cls.root = Path(cls.temporary.name)
        cls.video = cls.root / "black then white.mp4"
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=black:size=160x90:rate=20:d=2",
                "-f", "lavfi", "-i", "color=c=white:size=160x90:rate=20:d=2",
                "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "20",
                str(cls.video),
            ],
            check=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_queue_state_round_trips_and_resets_interrupted_work(self) -> None:
        state = self.root / "batch.json"
        configuration = BatchConfiguration(
            videos=[BatchVideoEntry(str(self.video), manual_review=True, status="analysing", progress=43)],
            character_ids=["alex", "sam"],
            output_dir=str(self.root / "outputs"),
            arrangement="separate",
            quality="rapid",
            trash_after_success=True,
        )
        save_batch_configuration(configuration, state)
        restored = load_batch_configuration(state)
        self.assertEqual(restored.character_ids, ["alex", "sam"])
        self.assertTrue(restored.videos[0].manual_review)
        self.assertEqual(restored.videos[0].status, "queued")
        self.assertTrue(restored.trash_after_success)

    def test_folder_discovery_and_character_filename_safety(self) -> None:
        nested = self.root / "nested"
        nested.mkdir(exist_ok=True)
        (nested / "movie.MKV").write_bytes(b"video")
        (nested / "notes.txt").write_text("ignore")
        self.assertEqual(discover_videos(self.root)[-1].name, "movie.MKV")
        self.assertEqual(safe_name_component("  Merve Ekinci / Özel  "), "Merve_Ekinci_Özel")

    def test_one_decode_pass_matches_multiple_characters(self) -> None:
        engine = MultiTargetEngine()
        targets = [
            BatchCharacterTarget("dark", "Dark", [("dark.jpg", b"dark")], 0.4),
            BatchCharacterTarget("bright", "Bright", [("bright.jpg", b"bright")], 0.4),
        ]
        results = analyse_video_for_characters(
            self.video,
            engine,
            targets,
            duration_ms=4000,
            quality="balanced",
        )
        by_id = {item.character_id: item for item in results}
        self.assertTrue(by_id["dark"].hits)
        self.assertTrue(by_id["bright"].hits)
        self.assertLessEqual(engine.shared_frames_checked, 4)
        self.assertLess(engine.frames_checked, 60)
        self.assertLessEqual(by_id["dark"].ranges[0].end_ms, 2200)
        self.assertGreaterEqual(by_id["bright"].ranges[0].start_ms, 1800)

    def test_hits_are_grouped_into_distinct_automatic_ranges(self) -> None:
        ranges = build_appearance_ranges(
            [AppearanceHit(5_000, 0.75), AppearanceHit(7_000, 0.82), AppearanceHit(20_000, 0.45)],
            30_000,
            2_000,
            0.40,
        )
        self.assertEqual(len(ranges), 2)
        self.assertEqual(ranges[0].origin, "automatic")
        self.assertEqual(ranges[1].origin, "automatic_uncertain")
        self.assertEqual(ranges[0].start_ms, 5_000)
        self.assertLessEqual(ranges[0].end_ms, 7_250)

    def test_source_cleanup_uses_gio_trash_only(self) -> None:
        source = self.root / "trash-me.mp4"
        source.write_bytes(b"source")

        def moved(command, **_kwargs):
            self.assertEqual(command[:2], ["/usr/bin/gio", "trash"])
            source.unlink()
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            patch("core.batch.shutil.which", return_value="/usr/bin/gio"),
            patch("core.batch.subprocess.run", side_effect=moved),
        ):
            move_source_to_trash(source)
        self.assertFalse(source.exists())

    def test_export_validation_checks_duration_and_decodes_the_tail(self) -> None:
        validate_export(self.video, expected_duration_ms=4000)
        with self.assertRaisesRegex(RuntimeError, "duration is incomplete"):
            validate_export(self.video, expected_duration_ms=8000)


if __name__ == "__main__":
    unittest.main()
