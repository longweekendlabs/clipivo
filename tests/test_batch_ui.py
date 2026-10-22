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

    def test_batch_page_queues_videos_and_people(self) -> None:
        page = BatchPage(self.store, AvailableEngine())
        try:
            self.assertEqual(page.job.source_after.currentData(), "keep")
            self.assertIn("Move to Trash", [page.job.source_after.itemText(row) for row in range(page.job.source_after.count())])
            page.characters.item(0).setCheckState(Qt.Checked)
            page.add_paths([self.video, self.video])
            self.assertEqual(page.queue.rowCount(), 1)
            self.assertEqual(page.queue.item(0, 1).text(), "source video.mp4")
            self.assertEqual(page.configuration.videos[0].person_ids, [self.alex.id])
            self.assertEqual(page.queue.cellWidget(0, 3).text(), "Alex")
            self.assertIn("Alex 1", page.summary.text())

            second = self.root / "second.mkv"
            second.write_bytes(b"video")
            page.characters.item(0).setCheckState(Qt.Unchecked)
            page.add_dropped([self.root])
            self.assertEqual(page.queue.rowCount(), 2)
            self.assertEqual(page.queue.cellWidget(1, 3).text(), "Choose who…")
            self.assertIn("1 without anyone", page.summary.text())
            with patch("ui.batch_page.QMessageBox.information") as told:
                page._start()
            self.assertIn("second.mkv", told.call_args.args[2])
            self.assertIsNone(page.worker)

            page.one_time.add([("guest.png", b"guest")])
            page.queue.selectAll()
            sam_row = next(row for row in range(page.characters.count()) if page.characters.item(row).text().startswith("Sam"))
            page.characters.item(sam_row).setCheckState(Qt.Checked)
            self.assertTrue(all(self.sam.id in entry.person_ids for entry in page.configuration.videos))
            self.assertIn("Sam", page.queue.cellWidget(1, 3).text())
            page.characters.item(sam_row).setCheckState(Qt.Unchecked)
            self.assertFalse(any(self.sam.id in entry.person_ids for entry in page.configuration.videos))
            page._toggle_person(page.configuration.videos[0], self.sam.id, True)
            self.assertTrue(all(self.sam.id in entry.person_ids for entry in page.configuration.videos))
            page.queue.clearSelection()
            page._toggle_person(page.configuration.videos[1], self.sam.id, False)
            self.assertIn(self.sam.id, page.configuration.videos[0].person_ids)
            self.assertNotIn(self.sam.id, page.configuration.videos[1].person_ids)
            self.assertEqual(page.configuration.videos[0].person_ids[0], self.alex.id)
        finally:
            page.close()

    def test_time_left_uses_this_runs_speed_and_ignores_pauses(self) -> None:
        page = BatchPage(self.store, AvailableEngine())
        try:
            first = BatchVideoEntry(str(self.video), person_ids=[self.alex.id], duration_ms=600_000, status="completed", progress=100)
            second = BatchVideoEntry(str(self.video), person_ids=[self.alex.id], duration_ms=1_200_000, status="analysing", progress=50)
            page.configuration.videos = [first, second]
            page._run_ids = {first.id, second.id}
            with patch("ui.batch_page.time.monotonic", return_value=1000.0):
                page._run_started = 1000.0 - 160.0
                page._paused_total = 40.0  # 120 s of work handled 600 s + 600 s of video
                text = page._time_left(second)
            self.assertIn("Video 2 of 2", text)
            self.assertIn("about 1 min left for this one", text)
            self.assertIn("about 1 min for all", text)
            restored = BatchVideoEntry.from_dict(second.to_dict())
            self.assertEqual(restored.duration_ms, 1_200_000)
        finally:
            page.close()

    def test_each_video_is_searched_only_for_its_own_people(self) -> None:
        other = self.root / "other.mp4"
        other.write_bytes(b"video")
        configuration = BatchConfiguration(
            videos=[
                BatchVideoEntry(str(self.video), person_ids=[self.alex.id]),
                BatchVideoEntry(str(other), person_ids=[self.sam.id]),
            ],
            mode="accurate",
        )
        targets = [
            BatchCharacterTarget(self.alex.id, self.alex.name, [("alex.jpg", b"alex")], 0.4),
            BatchCharacterTarget(self.sam.id, self.sam.name, [("sam.jpg", b"sam")], 0.4),
        ]
        seen = []

        def fake_analyse(source, _engine, entry_targets, **_kwargs):
            seen.append((Path(source).name, [target.name for target in entry_targets]))
            return [CharacterAppearances(target.id, target.name, (), ()) for target in entry_targets]

        worker = BatchExecutionWorker(configuration, targets, AvailableEngine())
        with (
            patch("ui.batch_page.probe_video", return_value=type("Info", (), {"duration_ms": 4000, "fps": 25.0})()),
            patch("ui.batch_page.analyse_video_for_characters", side_effect=fake_analyse),
        ):
            worker.run()
        self.assertEqual(seen, [("source video.mp4", ["Alex"]), ("other.mp4", ["Sam"])])

    def test_worker_exports_each_character_then_trashes_only_after_validation(self) -> None:
        output_dir = self.root / "outputs"
        configuration = BatchConfiguration(
            videos=[BatchVideoEntry(str(self.video), person_ids=[self.alex.id, self.sam.id])],
            output_dir=str(output_dir),
            arrangement="compilation",
            source_after="trash",
            save_next_to_source=False,
            mode="accurate",
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
            patch("core.batch.move_source_to_trash") as trash,
        ):
            worker.run()
        self.assertEqual([call.kwargs["output_character"] for call in export.call_args_list], ["Alex", "Sam"])
        self.assertTrue(all(call.args[2] == output_dir for call in export.call_args_list))
        self.assertTrue(all(call.kwargs["source_video_bitrate_kbps"] == 900 for call in export.call_args_list))
        self.assertEqual(validate.call_count, 2)
        self.assertTrue(all(call.kwargs["expected_duration_ms"] == 2000 for call in validate.call_args_list))
        trash.assert_called_once_with(self.video)
        self.assertEqual(updates[-1][1], "completed")

    def test_same_folder_option_saves_next_to_each_source(self) -> None:
        configuration = BatchConfiguration(videos=[BatchVideoEntry(str(self.video))], output_dir="/elsewhere", save_next_to_source=True)
        self.assertEqual(configuration.output_dir_for(self.video), self.video.parent)
        configuration.save_next_to_source = False
        self.assertEqual(configuration.output_dir_for(self.video), Path("/elsewhere"))
        restored = BatchConfiguration.from_dict(configuration.to_dict())
        self.assertFalse(restored.save_next_to_source)

    def test_no_match_never_trashes_source(self) -> None:
        configuration = BatchConfiguration(
            videos=[BatchVideoEntry(str(self.video), person_ids=[self.alex.id])],
            output_dir=str(self.root / "outputs"),
            source_after="trash",
        )
        target = BatchCharacterTarget(self.alex.id, self.alex.name, [("alex.jpg", b"alex")], 0.4)
        result = CharacterAppearances(target.id, target.name, (), ())
        worker = BatchExecutionWorker(configuration, [target], AvailableEngine())
        updates = []
        worker.entryUpdated.connect(lambda *args: updates.append(args))
        with (
            patch("ui.batch_page.probe_video", return_value=type("Info", (), {"duration_ms": 4000, "fps": 25.0})()),
            patch("ui.batch_page.analyse_video_for_characters", return_value=[result]),
            patch("core.batch.move_source_to_trash") as trash,
        ):
            worker.run()
        trash.assert_not_called()
        self.assertEqual(updates[-1][1], "no_match")

    def test_failed_tail_validation_removes_bad_output_and_keeps_source(self) -> None:
        configuration = BatchConfiguration(
            videos=[BatchVideoEntry(str(self.video), person_ids=[self.alex.id])],
            output_dir=str(self.root / "outputs"),
            source_after="trash",
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
            patch("core.batch.move_source_to_trash") as trash,
        ):
            worker.run()
        self.assertFalse(bad_output.exists())
        trash.assert_not_called()
        self.assertEqual(updates[-1][1], "failed")


if __name__ == "__main__":
    unittest.main()
