from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from core.batch import (
    AppearanceHit,
    BatchCharacterTarget,
    BatchConfiguration,
    BatchVideoEntry,
    CharacterAppearances,
)
from core.character_store import CharacterStore
from core.face_engine import EngineStatus
from core.models import ClipRange
from ui.batch_page import BatchExecutionWorker, BatchPage


class AvailableEngine:
    def status(self):
        return EngineStatus(True, "GPU", "Test engine")


class BatchUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="clipivo-batch-ui-")
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {"XDG_DATA_HOME": str(self.root / "data")})
        self.environment.start()
        self.store = CharacterStore(self.root / "characters", encryption_key=b"b" * 32)
        self.alex = self.store.create_character("Alex")
        self.store.add_reference(self.alex.id, "alex.jpg", b"alex-reference")
        self.sam = self.store.create_character("Sam")
        self.store.add_reference(self.sam.id, "sam.jpg", b"sam-reference")
        self.video = self.root / "source video.mp4"
        self.video.write_bytes(b"video")

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    def test_batch_page_queues_videos_characters_and_review_flags(self) -> None:
        page = BatchPage(self.store, AvailableEngine())
        try:
            page.add_paths([self.video, self.video])
            self.assertEqual(page.queue.rowCount(), 1)
            self.assertEqual(page.queue.item(0, 1).text(), "source video.mp4")
            self.assertFalse(page.trash_source.isChecked())
            self.assertIn("Trash", page.trash_source.toolTip())
            page.queue.item(0, 2).setCheckState(Qt.Checked)
            self.assertTrue(page.configuration.videos[0].manual_review)
            page.characters.item(0).setCheckState(Qt.Checked)
            self.assertEqual(len(page._selected_character_ids()), 1)
            self.assertIn("1 video", page.summary.text())
            self.assertIn("1 selected character", page.summary.text())
        finally:
            page.close()

    def test_worker_exports_each_character_then_trashes_only_after_validation(self) -> None:
        output_dir = self.root / "outputs"
        configuration = BatchConfiguration(
            videos=[BatchVideoEntry(str(self.video))],
            output_dir=str(output_dir),
            arrangement="compilation",
            trash_after_success=True,
        )
        targets = [
            BatchCharacterTarget(self.alex.id, self.alex.name, [("alex.jpg", b"alex")], 0.4),
            BatchCharacterTarget(self.sam.id, self.sam.name, [("sam.jpg", b"sam")], 0.4),
        ]
        results = [
            CharacterAppearances(target.id, target.name, (AppearanceHit(1000, 0.9),), (ClipRange(0, 2000, "automatic"),))
            for target in targets
        ]

        def fake_export(_source, _ranges, folder, _mode, _stitched, **kwargs):
            target = Path(folder) / f"{kwargs['output_character']}.mp4"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"output")
            return [target]

        worker = BatchExecutionWorker(configuration, targets, AvailableEngine())
        updates = []
        worker.entryUpdated.connect(lambda *args: updates.append(args))
        with (
            patch("ui.batch_page.probe_video", return_value=type(
                "Info", (), {"duration_ms": 4000, "fps": 25.0, "video_bitrate_kbps": 900}
            )()),
            patch("ui.batch_page.analyse_video_for_characters", return_value=results),
            patch("ui.batch_page.export_ranges", side_effect=fake_export) as export,
            patch("ui.batch_page.validate_export") as validate,
            patch("ui.batch_page.move_source_to_trash") as trash,
        ):
            worker.run()
        self.assertEqual([call.kwargs["output_character"] for call in export.call_args_list], ["Alex", "Sam"])
        self.assertTrue(all(call.kwargs["source_video_bitrate_kbps"] == 900 for call in export.call_args_list))
        self.assertEqual(validate.call_count, 2)
        self.assertTrue(all(call.kwargs["expected_duration_ms"] == 2000 for call in validate.call_args_list))
        trash.assert_called_once_with(self.video)
        self.assertEqual(updates[-1][1], "completed")

    def test_no_match_never_trashes_source(self) -> None:
        configuration = BatchConfiguration(
            videos=[BatchVideoEntry(str(self.video))],
            output_dir=str(self.root / "outputs"),
            trash_after_success=True,
        )
        target = BatchCharacterTarget(self.alex.id, self.alex.name, [("alex.jpg", b"alex")], 0.4)
        result = CharacterAppearances(target.id, target.name, (), ())
        worker = BatchExecutionWorker(configuration, [target], AvailableEngine())
        updates = []
        worker.entryUpdated.connect(lambda *args: updates.append(args))
        with (
            patch("ui.batch_page.probe_video", return_value=type("Info", (), {"duration_ms": 4000, "fps": 25.0})()),
            patch("ui.batch_page.analyse_video_for_characters", return_value=[result]),
            patch("ui.batch_page.move_source_to_trash") as trash,
        ):
            worker.run()
        trash.assert_not_called()
        self.assertEqual(updates[-1][1], "no_match")

    def test_failed_tail_validation_removes_bad_output_and_keeps_source(self) -> None:
        configuration = BatchConfiguration(
            videos=[BatchVideoEntry(str(self.video))],
            output_dir=str(self.root / "outputs"),
            trash_after_success=True,
        )
        target = BatchCharacterTarget(self.alex.id, self.alex.name, [("alex.jpg", b"alex")], 0.4)
        result = CharacterAppearances(
            target.id, target.name, (AppearanceHit(1000, 0.9),), (ClipRange(500, 1500, "automatic"),)
        )
        bad_output = self.root / "outputs" / "bad.mp4"

        def fake_export(*_args, **_kwargs):
            bad_output.parent.mkdir(parents=True, exist_ok=True)
            bad_output.write_bytes(b"broken")
            return [bad_output]

        worker = BatchExecutionWorker(configuration, [target], AvailableEngine())
        updates = []
        worker.entryUpdated.connect(lambda *args: updates.append(args))
        with (
            patch("ui.batch_page.probe_video", return_value=type(
                "Info", (), {"duration_ms": 4000, "fps": 25.0, "video_bitrate_kbps": 900}
            )()),
            patch("ui.batch_page.analyse_video_for_characters", return_value=[result]),
            patch("ui.batch_page.export_ranges", side_effect=fake_export),
            patch("ui.batch_page.validate_export", side_effect=RuntimeError("tail decode failed")),
            patch("ui.batch_page.move_source_to_trash") as trash,
        ):
            worker.run()
        self.assertFalse(bad_output.exists())
        trash.assert_not_called()
        self.assertEqual(updates[-1][1], "failed")


if __name__ == "__main__":
    unittest.main()
