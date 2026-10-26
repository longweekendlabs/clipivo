from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from core.models import ClipRange
from core.face_engine import EngineStatus
from core.filters import create_filter
from core.video_controller import DecodedFrame
from core.video_probe import probe_video
from ui.filters_page import FilterConfigDialog
from ui.main_window import MainWindow
from ui.vlc_player import VlcPlaybackController


class ManualUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.temporary = tempfile.TemporaryDirectory(prefix="clipivo-ui-tests-")
        cls.environment = patch.dict("os.environ", {"XDG_DATA_HOME": str(Path(cls.temporary.name) / "data")})
        cls.environment.start()
        cls.video = Path(cls.temporary.name) / "ui-test.mp4"
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25",
                "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "25",
                str(cls.video),
            ],
            check=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.environment.stop()
        cls.temporary.cleanup()

    def test_open_step_mark_and_add_range(self) -> None:
        window = MainWindow(Path(__file__).resolve().parents[1])
        try:
            window._video_opened(probe_video(self.video))
            self.assertFalse(window.preview.source.isNull())
            window._step_frame(True)
            self.assertEqual(window.current_ms, 40)
            window._set_marker_a()
            window._seek(1000)
            window._set_marker_b()
            window._add_manual_range()
            clips = window.right_panel.ranges()
            self.assertEqual(len(clips), 1)
            self.assertEqual((clips[0].start_ms, clips[0].end_ms), (40, 1000))
        finally:
            window.close()

    def test_timeline_drag_defers_media_and_frame_seek_until_release(self) -> None:
        window = MainWindow(Path(__file__).resolve().parents[1])
        try:
            window._video_opened(probe_video(self.video))
            with patch.object(window.player, "setPosition") as set_position:
                window._on_timeline_changed(500)
                set_position.assert_not_called()
                window._timeline_scrubbing = True
                with patch.object(window.player, "previewPosition") as preview_request:
                    window._on_timeline_changed(600)
                    preview_request.assert_called_once_with(600)
                window._timeline_scrubbing = False
                with patch.object(window, "_decode_current_position") as decode:
                    window._on_scrub_finished(500)
                    set_position.assert_called_once_with(500)
                    decode.assert_called_once_with()
        finally:
            window.close()

    def test_seek_commits_the_timestamp_of_the_frame_actually_displayed(self) -> None:
        window = MainWindow(Path(__file__).resolve().parents[1])
        try:
            window._video_opened(probe_video(self.video))
            decoded = DecodedFrame(960, 2, 2, b"\0" * 12, 6)
            with patch.object(window.controller, "frame_at", return_value=decoded):
                window._seek(1_000)
            self.assertEqual(window.current_ms, 960)
            self.assertEqual(window.timeline.value(), 960)
            self.assertEqual(window.player.position(), 960)
        finally:
            window.close()

    def test_save_uses_valid_current_selection_when_clip_list_is_empty(self) -> None:
        window = MainWindow(Path(__file__).resolve().parents[1])
        try:
            window._video_opened(probe_video(self.video))
            window.right_panel.set_clips([])
            window._set_marker_a()
            window._seek(750)
            window._set_marker_b()
            expected_end = window.marker_b
            self.assertEqual(window.right_panel.ranges(), [])
            exportable = window._enabled_clips_for_export()
            self.assertEqual(len(exportable), 1)
            self.assertEqual((exportable[0].start_ms, exportable[0].end_ms), (0, expected_end))
            self.assertEqual(len(window.right_panel.ranges()), 1)
        finally:
            window.close()

    def test_transport_icons_and_play_pause_state_are_distinct(self) -> None:
        window = MainWindow(Path(__file__).resolve().parents[1])
        try:
            previous_image = window.transport_by_action["previous_frame"].icon().pixmap(20, 20).toImage()
            play_image = window.transport_by_action["play"].icon().pixmap(20, 20).toImage()
            next_image = window.transport_by_action["next_frame"].icon().pixmap(20, 20).toImage()
            self.assertNotEqual(previous_image, play_image)
            self.assertNotEqual(next_image, play_image)
            self.assertNotEqual(previous_image, next_image)
            window._on_playback_state_changed(True)
            self.assertIn("Pause", window.play_button.toolTip())
            window._on_playback_state_changed(False)
            self.assertIn("Play", window.play_button.toolTip())
        finally:
            window.close()

    def test_vlc_holds_requested_start_until_the_input_accepts_seek(self) -> None:
        player = VlcPlaybackController()
        fake = MagicMock()
        fake.play.return_value = 0
        fake.get_state.return_value = "playing"
        fake.get_time.side_effect = [0, 250_100]
        player._player = fake
        player._vlc = SimpleNamespace(State=SimpleNamespace(
            Playing="playing",
            Opening="opening",
            Buffering="buffering",
            Error="error",
        ))
        player._source = Path("source.mp4")

        player.setPosition(250_000)
        player.play()
        player._poll()
        fake.set_time.assert_called_with(250_000)
        self.assertEqual(player._pending_position, 250_000)

        player._poll()
        self.assertIsNone(player._seek_target)
        self.assertEqual(player._pending_position, 250_100)

    def test_vlc_resumes_paused_surface_for_live_scrub_frames(self) -> None:
        player = VlcPlaybackController()
        fake = MagicMock()
        fake.get_state.return_value = "paused"
        fake.get_time.return_value = 12_345
        player._player = fake
        player._vlc = SimpleNamespace(State=SimpleNamespace(
            Playing="playing",
            Paused="paused",
            Opening="opening",
            Buffering="buffering",
            Error="error",
        ))
        player._source = Path("source.mp4")

        player.beginPreview(20_000)
        fake.set_pause.assert_called_with(0)
        fake.audio_set_mute.assert_called_with(True)
        self.assertEqual(player._seek_target, 20_000)

        player.endPreview()
        fake.set_pause.assert_called_with(1)
        fake.audio_set_mute.assert_called_with(False)

    def test_output_choices_explain_themselves_and_drive_save(self) -> None:
        window = MainWindow(Path(__file__).resolve().parents[1])
        try:
            window._video_opened(probe_video(self.video))
            job = window.right_panel.job
            job.quality.setValue(20)
            job.max_size.setCurrentIndex(0)
            job.framing.setCurrentIndex(0)
            self.assertEqual(job.quality_label.text(), "Original")
            self.assertIn("identical", job.output_hint.text())
            job.framing.setCurrentIndex(job.framing.findData("portrait"))
            self.assertIn("95%", job.output_hint.text())
            job.framing.setCurrentIndex(0)
            job.quality.setValue(18)
            self.assertEqual(job.values()["output_quality"], 90)
            self.assertEqual(job.values()["mode"], "accurate")
            self.assertEqual(job.destination.itemText(0), "Same folder as video")
            self.assertEqual(job.destination.itemData(job.destination.count() - 1), "__choose_folder__")
            job.arrangement.setCurrentIndex(job.arrangement.findData("compilation"))
            job.include_audio.setChecked(False)
            with patch.object(window, "_request_export") as request_export:
                window.right_panel.save_button.click()
                request_export.assert_called_once_with("accurate", True, False)
        finally:
            window.close()

    def test_export_acceleration_setting_updates_main_window(self) -> None:
        window = MainWindow(Path(__file__).resolve().parents[1])
        original = window.settings_page.export_encoder.currentData()
        try:
            index = window.settings_page.export_encoder.findData("cpu")
            window.settings_page.export_encoder.setCurrentIndex(index)
            self.assertEqual(window.export_video_encoder, "cpu")
            self.assertIn("NVENC", window.settings_page.export_encoder.toolTip())
        finally:
            window.settings_page.export_encoder.setCurrentIndex(
                window.settings_page.export_encoder.findData(original)
            )
            window.close()

    def test_frame_screenshot_shortcut_saves_source_resolution_png(self) -> None:
        window = MainWindow(Path(__file__).resolve().parents[1])
        target = Path(self.temporary.name) / "captured-frame.png"
        try:
            self.assertEqual(window.actions["screenshot"].shortcut(), QKeySequence("Ctrl+P"))
            self.assertEqual(window.actions["close_video"].shortcut(), QKeySequence("Ctrl+W"))
            self.assertFalse(window.actions["screenshot"].isEnabled())
            window._video_opened(probe_video(self.video))
            self.assertTrue(window.actions["screenshot"].isEnabled())
            with (
                patch("ui.main_window.date_tag", return_value="260724_1314_005"),
                patch("ui.main_window.choose_png_file", return_value=str(target)) as save_dialog,
            ):
                window._save_current_frame()
            self.assertEqual(
                save_dialog.call_args.args[2],
                "ui-test_260724_1314_005--00:00:00.000.png",
            )
            image = QImage(str(target))
            self.assertFalse(image.isNull())
            self.assertEqual((image.width(), image.height()), (320, 180))
            self.assertIn("Frame saved", window.ready_text.text())
        finally:
            window.close()

    def test_play_selection_and_audio_controls(self) -> None:
        window = MainWindow(Path(__file__).resolve().parents[1])
        try:
            window._video_opened(probe_video(self.video))
            window.marker_a = 240
            window.marker_b = 880
            with patch.object(window, "_start_playback") as start_playback:
                window._play_selection()
                self.assertEqual(window.current_ms, 240)
                self.assertEqual(window.selection_play_end_ms, 880)
                start_playback.assert_called_once_with()

            self.assertFalse(window.playback_muted)
            window._toggle_playback_mute()
            self.assertTrue(window.playback_muted)
            self.assertIn("Unmute", window.volume_button.toolTip())
            window._toggle_playback_mute()
            self.assertFalse(window.playback_muted)
            self.assertIn("Mute", window.volume_button.toolTip())
        finally:
            window.close()

    def test_clip_pane_collapse_reclaims_its_width(self) -> None:
        window = MainWindow(Path(__file__).resolve().parents[1])
        try:
            window.show()
            self.app.processEvents()
            expanded_width = window.side_container.width()
            editor_width = window.video_splitter.widget(0).width()

            window._collapse_right()
            self.app.processEvents()
            self.assertEqual(window.side_container.width(), 24)
            self.assertFalse(window.right_panel.isVisible())
            self.assertGreater(window.video_splitter.widget(0).width(), editor_width + 200)
            self.assertTrue(window.restore_pane_button.isVisible())
            self.assertIn("Restore", window.restore_pane_button.toolTip())

            window._restore_right()
            self.app.processEvents()
            self.assertGreaterEqual(window.side_container.width(), 264)
            self.assertTrue(window.right_panel.isVisible())
            self.assertGreaterEqual(expanded_width, 264)
            self.assertIn("Collapse", window.right_panel.collapse_button.toolTip())
        finally:
            window.close()

    def test_top_toolbar_navigation_replaces_left_rail(self) -> None:
        window = MainWindow(Path(__file__).resolve().parents[1])
        try:
            self.assertFalse(hasattr(window, "rail"))
            self.assertEqual(window.page_tabs.count(), 5)
            self.assertEqual(window.page_tabs.tabText(1), "Filters")
            self.assertEqual(window.page_tabs.tabText(2), "Batch")
            self.assertEqual(window.pages.currentIndex(), 0)
            window._video_opened(probe_video(self.video))
            window.show()
            self.app.processEvents()
            window.actions["filters_page"].trigger()
            self.app.processEvents()
            self.assertEqual(window.pages.currentIndex(), 0)
            self.assertEqual(window.page_tabs.currentIndex(), 1)
            self.assertTrue(window.filters_dialog.isVisible())
            self.assertEqual(window.filters_page.workspace.currentIndex(), 0)
            self.assertFalse(window.filters_page.preview_canvas.isVisible())
            window.actions["people_page"].trigger()
            self.assertEqual(window.pages.currentIndex(), 2)
            self.assertEqual(window.page_tabs.currentIndex(), 3)
            self.assertFalse(window.filters_dialog.isVisible())
            window.actions["settings"].trigger()
            self.assertEqual(window.pages.currentIndex(), 3)
            self.assertEqual(window.page_tabs.currentIndex(), 4)
            window.actions["video_page"].trigger()
            self.assertEqual(window.pages.currentIndex(), 0)
            self.assertEqual(window.page_tabs.currentIndex(), 0)
        finally:
            window.close()

    def test_save_clips_writes_next_to_the_source_without_asking(self) -> None:
        window = MainWindow(Path(__file__).resolve().parents[1])
        try:
            window._video_opened(probe_video(self.video))
            window.right_panel.set_clips([ClipRange(0, 1000)])
            window.right_panel.job.set_values({"save_next_to_source": True, "arrangement": "separate"})
            with (
                patch("ui.main_window.choose_save_file") as save_dialog,
                patch("ui.main_window.choose_directory") as folder_dialog,
                patch.object(window, "_start_export") as start_export,
            ):
                window._request_export("accurate", False, True)
            save_dialog.assert_not_called()
            folder_dialog.assert_not_called()
            start_export.assert_called_once_with(self.video.parent, "accurate", False, True, None)
        finally:
            window.close()

    def test_start_results_replace_automatic_clips_and_keep_manual_ones(self) -> None:
        window = MainWindow(Path(__file__).resolve().parents[1])
        try:
            window._video_opened(probe_video(self.video))
            manual = ClipRange(0, 200, origin="manual")
            window.right_panel.set_clips([manual, ClipRange(600, 800, origin="automatic")])
            found = [ClipRange(300, 700, origin="automatic"), ClipRange(1200, 1800, origin="automatic_uncertain")]
            with patch("ui.main_window.save_ranges"):
                window._video_job_ranges("entry", "person", "Presenter", found)
            clips = window.right_panel.ranges()
            self.assertEqual([(c.start_ms, c.end_ms) for c in clips], [(0, 200), (300, 700), (1200, 1800)])
            self.assertEqual(window.timeline.detections, [300, 1200])
            self.assertEqual(window.detected_appearances, [])
            self.assertEqual((window.marker_a, window.marker_b), (300, 700))
        finally:
            window.close()

    def test_start_queues_this_video_for_the_one_time_person(self) -> None:
        engine = MagicMock()
        engine.status.return_value = EngineStatus(True, "GPU", "Test engine")
        window = MainWindow(Path(__file__).resolve().parents[1], face_engine=engine)
        try:
            window._video_opened(probe_video(self.video))
            window.one_time_person.add([("a.png", b"face-a"), ("b.png", b"face-b")])
            self.assertEqual(window.current_target.name, "One-time person")
            window.right_panel.job.set_values({"arrangement": "compilation", "save_next_to_source": True, "mode": "accurate"})
            with patch("ui.main_window.BatchExecutionWorker") as worker_class, patch("ui.main_window.offer_downloads", return_value=True):
                window._start_video_job()
            configuration, targets, _engine = worker_class.call_args.args[:3]
            self.assertEqual([entry.path for entry in configuration.videos], [self.video])
            self.assertTrue(configuration.save_next_to_source)
            self.assertEqual(configuration.arrangement, "compilation")
            self.assertEqual(targets[0].references, [("a.png", b"face-a"), ("b.png", b"face-b")])
            worker_class.return_value.start.assert_called_once()
            self.assertFalse(window.right_panel.job.start_button.isEnabled())

            window._video_job_updated("entry", "completed", 100, "Done · 1 clip · 0:01", [str(self.video.parent / "out.mp4")])
            window._video_job_finished(False)
            self.assertIn("Done", window.right_panel.job.status.text())
            self.assertFalse(window.right_panel.job.open_folder_button.isHidden())
            self.assertTrue(window.right_panel.job.start_button.isEnabled())
        finally:
            window.close()

    def test_double_marker_press_sets_the_opposite_marker(self) -> None:
        window = MainWindow(Path(__file__).resolve().parents[1])
        try:
            window._video_opened(probe_video(self.video))
            window.show()
            self.app.processEvents()
            window._seek(400)
            QTest.mouseDClick(window.marker_buttons[1], Qt.LeftButton)
            self.assertEqual(window.marker_a, 400)
            window._seek(800)
            QTest.mouseDClick(window.marker_buttons[0], Qt.LeftButton)
            self.assertEqual(window.marker_b, 800)
        finally:
            window.close()

    def test_overlapping_markers_are_reported_as_combined(self) -> None:
        window = MainWindow(Path(__file__).resolve().parents[1])
        try:
            window._video_opened(probe_video(self.video))
            window._seek(600)
            window._set_marker_a()
            window._set_marker_b()
            self.assertEqual((window.marker_a, window.marker_b), (600, 600))
            self.assertIn("A and B", window.ready_text.text())
            self.assertIn("A and B overlap", window.timeline.accessibleDescription())
        finally:
            window.close()

    def test_filters_are_grouped_and_force_exact_export(self) -> None:
        window = MainWindow(Path(__file__).resolve().parents[1])
        try:
            window._video_opened(probe_video(self.video))
            page = window.filters_page
            transformation = [page.available.item(row).text() for row in range(page.available.count())]
            self.assertEqual(transformation, ["Crop", "Rotate", "Vertical flip", "Frame rate"])
            page.categories.setCurrentRow(1)
            colors = [page.available.item(row).text() for row in range(page.available.count())]
            self.assertEqual(colors, ["Contrast", "Color balance"])

            page.categories.setCurrentRow(0)
            page.available.setCurrentRow(0)
            with patch.object(FilterConfigDialog, "exec", return_value=QDialog.Accepted):
                page._add_filter()
            for dialog in window.findChildren(FilterConfigDialog):
                dialog.done(QDialog.Accepted)
            self.assertEqual([item.kind for item in page.filters()], ["crop"])
            self.assertEqual(window.right_panel.job.values()["mode"], "accurate")
            self.assertEqual(window.right_panel.job.quality.maximum(), 19)

            page._remove_filter()
            self.assertEqual(page.filters(), [])
            self.assertEqual(window.right_panel.job.quality.maximum(), 20)
            self.assertEqual(window.right_panel.job.values()["mode"], "copy")

            for category_row, available_row, expected in ((0, 1, "Rotate"), (1, 0, "Contrast")):
                page.show_manager()
                page.categories.setCurrentRow(category_row)
                page.available.setCurrentRow(available_row)
                with patch.object(FilterConfigDialog, "exec", return_value=QDialog.Accepted):
                    page._add_filter()
                for dialog in window.findChildren(FilterConfigDialog):
                    dialog.done(QDialog.Accepted)
                self.assertEqual(page.workspace.currentIndex(), 0)
                self.assertIn(expected, page.active.currentItem().text())
                page._remove_filter()
        finally:
            window.close()

    def test_crop_editor_is_preview_first_and_drag_handles_update_values(self) -> None:
        window = MainWindow(Path(__file__).resolve().parents[1])
        try:
            window._video_opened(probe_video(self.video))
            window.resize(1280, 760)
            window.show()
            window._select_page(1)
            page = window.filters_page
            candidate = create_filter("crop", page.video_info.fps)
            dialog = FilterConfigDialog(
                page.video_info,
                page.source_pixmap,
                page.current_ms,
                [],
                candidate,
                window,
            )
            dialog.resize(1100, 760)
            dialog.show()
            self.app.processEvents()

            self.assertTrue(dialog.preview_canvas.crop_editing)

            crop_rect = dialog.preview_canvas._crop_rect()
            start_point = QPoint(crop_rect.left(), crop_rect.center().y())
            QTest.mousePress(dialog.preview_canvas, Qt.LeftButton, pos=start_point)
            QTest.mouseMove(dialog.preview_canvas, QPoint(start_point.x() + 80, start_point.y()))
            QTest.mouseRelease(dialog.preview_canvas, Qt.LeftButton, pos=QPoint(start_point.x() + 80, start_point.y()))
            self.app.processEvents()

            crop = dialog.result_filter()
            self.assertGreater(crop.params["left"], 0)
            self.assertEqual(crop.params["left"] % 2, 0)
            self.assertEqual(dialog._crop_spins["left"].value(), crop.params["left"])
            dialog.reject()
        finally:
            window.close()

    def test_canceling_filter_configuration_does_not_add_it(self) -> None:
        window = MainWindow(Path(__file__).resolve().parents[1])
        try:
            window._video_opened(probe_video(self.video))
            page = window.filters_page
            page.categories.setCurrentRow(1)
            page.available.setCurrentRow(1)
            with patch.object(FilterConfigDialog, "exec", return_value=QDialog.Rejected):
                page._add_filter()
            for dialog in window.findChildren(FilterConfigDialog):
                dialog.done(QDialog.Rejected)
            self.assertEqual(page.filters(), [])
        finally:
            window.close()

    def test_filter_preview_play_and_play_ab_controls(self) -> None:
        window = MainWindow(Path(__file__).resolve().parents[1])
        try:
            window._video_opened(probe_video(self.video))
            page = window.filters_page
            page.set_markers(200, 1200)
            contrast = create_filter("contrast")
            contrast.params.update(contrast=1.4, brightness=0.2)
            dialog = FilterConfigDialog(
                page.video_info,
                page.source_pixmap,
                400,
                [],
                contrast,
                window,
                marker_a=page.marker_a,
                marker_b=page.marker_b,
            )
            self.assertTrue(dialog.play_ab_button.isEnabled())
            dialog._toggle_playback(True)
            QTest.qWait(350)
            self.assertIsNotNone(dialog._play_process)
            self.assertIn("Pause", dialog.play_ab_button.text())
            self.assertEqual(dialog._play_start_ms, 200)
            self.assertEqual(dialog._play_end_ms, 1200)
            self.assertGreater(dialog.current_ms, 200)
            self.assertFalse(dialog.preview_canvas.output_pixmap.isNull())
            self.assertFalse(dialog.preview_canvas.input_pixmap.isNull())
            dialog.peek_button.click()
            self.assertEqual(dialog.preview_canvas.mode, "input")
            self.assertEqual(dialog.peek_button.text(), "Show filtered")
            original_frame_key = dialog.preview_canvas.input_pixmap.cacheKey()

            QTest.mouseClick(
                dialog.seek,
                Qt.LeftButton,
                pos=QPoint(round(dialog.seek.width() * 0.75), dialog.seek.height() // 2),
            )
            for _ in range(20):
                if dialog.preview_canvas.input_pixmap.cacheKey() != original_frame_key:
                    break
                QTest.qWait(50)
            self.assertIsNone(dialog._play_process)
            self.assertGreater(dialog.current_ms, 1400)
            self.assertNotEqual(dialog.preview_canvas.input_pixmap.cacheKey(), original_frame_key)
            self.assertEqual(dialog.play_button.text(), "▶ Play")
            dialog.reject()
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
