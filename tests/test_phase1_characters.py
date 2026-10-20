from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from core.character_store import CharacterStore
from core.face_engine import EngineStatus, ReferenceAssessment
from core.video_analysis import AppearanceMatch
from core.project_state import (
    load_find_result,
    load_target_selection,
    project_id,
    save_find_result,
    save_target_selection,
)
from ui.about_dialog import AboutDialog
from ui.main_window import FindCharacterWorker, MainWindow
from ui.people_page import discover_character_folders
from version import __org_name__, __version__


class FakeEngine:
    use_gpu = True
    detector_size = 640
    _application = None

    def status(self) -> EngineStatus:
        return EngineStatus(True, "GPU", "Test engine ready")


class PhaseOneCharacterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="clipivo-phase1-")
        self.root = Path(self.temporary.name)
        self.store = CharacterStore(self.root / "characters", encryption_key=b"x" * 32)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_reusable_and_adhoc_characters_are_isolated_and_encrypted(self) -> None:
        reusable = self.store.create_character("Reusable Alex")
        adhoc_a = self.store.create_character("Interview target", scope="adhoc", project_id="video-a")
        adhoc_b = self.store.create_character("Match target", scope="adhoc", project_id="video-b")
        reference = self.store.add_reference(reusable.id, "alex.png", b"private-image-bytes")

        self.assertEqual([item.id for item in self.store.characters()], [reusable.id])
        self.assertEqual(self.store.adhoc_for_project("video-a").id, adhoc_a.id)
        self.assertEqual(self.store.adhoc_for_project("video-b").id, adhoc_b.id)
        self.assertEqual(self.store.reference_bytes(reference.id), b"private-image-bytes")
        self.assertNotIn(b"Reusable Alex", self.store.index_path.read_bytes())
        self.assertNotIn(b"private-image-bytes", next(self.store.blobs.iterdir()).read_bytes())

    def test_project_target_selection_round_trips_outside_source_folder(self) -> None:
        source = self.root / "source.mp4"
        source.write_bytes(b"source")
        data_home = self.root / "data"
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(data_home)}):
            save_target_selection(source, "character-123", "adhoc")
            restored = load_target_selection(source)
            identity = project_id(source)
        self.assertEqual(restored, {"character_id": "character-123", "scope": "adhoc"})
        self.assertTrue((data_home / "clipivo" / "projects" / f"{identity}.json").is_file())

    def test_selected_target_enables_first_appearance_search(self) -> None:
        character = self.store.create_character("Jordan")
        self.store.add_reference(character.id, "jordan.jpg", b"jordan")
        source = self.root / "find-target.mp4"
        source.write_bytes(b"video")
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(self.root / "data")}):
            window = MainWindow(Path(__file__).resolve().parents[1], self.store, FakeEngine())
            try:
                window.video_info = type("Info", (), {"path": source})()
                window.video_job.refresh_people()
                window.video_job.select_person(character.id)
                window._job_person_changed(persist=False)
                self.assertEqual(window.current_target.id, character.id)
                self.assertTrue(window.actions["find_character"].isEnabled())
            finally:
                window.close()

    def test_find_character_escalates_fast_miss_before_not_found(self) -> None:
        fast_engine = object()
        accurate_engine = object()
        expected = AppearanceMatch(12_500, 0.81, 1, 8)
        worker = FindCharacterWorker(
            self.root / "source.mp4",
            0,
            60_000,
            fast_engine,
            accurate_engine,
            [("reference.jpg", b"reference")],
            0.40,
        )
        found = []
        worker.found.connect(found.append)
        with patch(
            "ui.main_window.find_first_appearance",
            side_effect=[None, None, expected],
        ) as scan:
            worker.run()

        self.assertEqual(found, [expected])
        self.assertEqual(
            [call.kwargs["sample_interval_ms"] for call in scan.call_args_list],
            [10_000, 5_000, 750],
        )
        self.assertEqual(
            [call.kwargs["keyframes_only"] for call in scan.call_args_list],
            [True, True, False],
        )
        self.assertIs(scan.call_args_list[0].args[1], fast_engine)
        self.assertIs(scan.call_args_list[1].args[1], accurate_engine)
        self.assertIs(scan.call_args_list[2].args[1], accurate_engine)

    def test_find_next_waits_for_current_appearance_to_end(self) -> None:
        fast_engine = object()
        accurate_engine = object()
        expected = AppearanceMatch(42_000, 0.83, 1, 5)
        worker = FindCharacterWorker(
            self.root / "source.mp4",
            12_500,
            60_000,
            fast_engine,
            accurate_engine,
            [("reference.jpg", b"reference")],
            0.40,
            12_500,
        )
        found = []
        worker.found.connect(found.append)
        with (
            patch("ui.main_window.find_end_of_appearance", return_value=20_000) as find_end,
            patch("ui.main_window.find_first_appearance", return_value=expected) as scan,
        ):
            worker.run()
        self.assertEqual(found, [expected])
        find_end.assert_called_once()
        self.assertEqual(scan.call_args.kwargs["start_ms"], 20_000)

    def test_successful_find_cache_invalidates_with_references_or_threshold(self) -> None:
        source = self.root / "cached-find.mp4"
        source.write_bytes(b"video")
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(self.root / "data")}):
            save_find_result(source, "alex", "refs-v1", 0.40, 12_345)
            self.assertEqual(load_find_result(source, "alex", "refs-v1", 0.40), 12_345)
            self.assertIsNone(load_find_result(source, "alex", "refs-v2", 0.40))
            self.assertIsNone(load_find_result(source, "alex", "refs-v1", 0.45))

    def test_picking_a_saved_person_is_remembered_for_the_video(self) -> None:
        character = self.store.create_character("Jordan")
        self.store.add_reference(character.id, "jordan.jpg", b"jordan")
        source = self.root / "video.mp4"
        source.write_bytes(b"video")
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(self.root / "data")}):
            window = MainWindow(Path(__file__).resolve().parents[1], self.store, FakeEngine())
            try:
                window.video_info = type("Info", (), {"path": source})()
                window.video_job.refresh_people()
                window.video_job.select_person(character.id)
                window._clear_current_target()
                window._job_person_changed(persist=True)
                self.assertEqual(window.current_target.id, character.id)
                self.assertEqual(load_target_selection(source)["character_id"], character.id)
            finally:
                window.close()

    def test_one_time_person_is_never_saved(self) -> None:
        legacy = self.store.create_character("Old ad-hoc", scope="adhoc", project_id="video-one")
        source = self.root / "video.mp4"
        source.write_bytes(b"video")
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(self.root / "data")}):
            window = MainWindow(Path(__file__).resolve().parents[1], self.store, FakeEngine())
            try:
                names = [window.video_job.person_combo.itemText(row) for row in range(window.video_job.person_combo.count())]
                self.assertFalse(any("Old ad-hoc" in name for name in names))
                window.video_info = type("Info", (), {"path": source})()
                window.one_time_person.add([("face.png", b"face")])
                self.assertEqual(window.current_target.scope, "session")
                self.assertEqual(load_target_selection(source), {})
                self.assertEqual(len(self.store.characters()), 0)
                self.assertIsNotNone(self.store.character(legacy.id))
            finally:
                window.close()

    def test_version_and_lwl_branding_are_visible(self) -> None:
        window = MainWindow(Path(__file__).resolve().parents[1], self.store, FakeEngine())
        try:
            self.assertIn(__version__, window.windowTitle())
            status_labels = [label.text() for label in window.statusBar().findChildren(type(window.ready_text))]
            self.assertTrue(any(__org_name__ in text and __version__ in text for text in status_labels))
            about = AboutDialog(window)
            self.assertTrue(any(__version__ in label.text() for label in about.findChildren(type(window.ready_text))))
        finally:
            window.close()

    def test_batch_folder_discovery_uses_immediate_folders_as_characters(self) -> None:
        root = self.root / "library"
        (root / "Alex" / "profiles").mkdir(parents=True)
        (root / "Sam").mkdir(parents=True)
        (root / "Alex" / "front.jpg").write_bytes(b"front")
        (root / "Alex" / "profiles" / "side.png").write_bytes(b"side")
        (root / "Sam" / "notes.txt").write_text("not an image")
        (root / "loose.jpg").write_bytes(b"ignored")

        discovered = discover_character_folders(root)
        self.assertEqual([name for name, _paths in discovered], ["Alex"])
        self.assertEqual(
            [path.relative_to(root / "Alex").as_posix() for path in discovered[0][1]],
            ["front.jpg", "profiles/side.png"],
        )

    def test_batch_import_creates_and_merges_reusable_characters(self) -> None:
        existing = self.store.create_character("Alex")
        window = MainWindow(Path(__file__).resolve().parents[1], self.store, FakeEngine())
        try:
            batches = [
                ("Alex", [("front.jpg", b"alex")], [ReferenceAssessment("front.jpg", True)]),
                ("Sam", [("sam.jpg", b"sam")], [ReferenceAssessment("sam.jpg", True)]),
            ]
            with patch("ui.people_page.QMessageBox.information"):
                window.people_page._batch_import_validated(batches)
            characters = {item.name: item for item in self.store.characters()}
            self.assertEqual(set(characters), {"Alex", "Sam"})
            self.assertEqual(characters["Alex"].id, existing.id)
            self.assertEqual(len(self.store.references(existing.id)), 1)
            self.assertEqual(len(self.store.references(characters["Sam"].id)), 1)
        finally:
            window.close()

    def test_people_actions_are_grouped_above_the_content(self) -> None:
        window = MainWindow(Path(__file__).resolve().parents[1], self.store, FakeEngine())
        try:
            window._select_page(3)
            window.show()
            self.app.processEvents()
            page = window.people_page
            self.assertLess(page.batch_button.mapTo(page, page.batch_button.rect().topLeft()).y(),
                            page.character_list.mapTo(page, page.character_list.rect().topLeft()).y())
            self.assertLess(page.add_references_button.mapTo(page, page.add_references_button.rect().topLeft()).y(),
                            page.reference_list.mapTo(page, page.reference_list.rect().topLeft()).y())
            self.assertIn("subfolder", page.batch_button.toolTip())
            self.assertIn("photos", page.add_references_button.toolTip())
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
