from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ui import native_dialogs


class NativeDialogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="clipivo-dialog-tests-")
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(
            os.environ,
            {"XDG_CURRENT_DESKTOP": "KDE", "XDG_CONFIG_HOME": str(self.root)},
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    @patch("ui.native_dialogs.QFileDialog.getOpenFileName")
    @patch("ui.native_dialogs.subprocess.run")
    @patch("ui.native_dialogs.shutil.which", return_value="/usr/bin/kdialog")
    def test_kde_uses_kdialog_for_video(self, _which, run, qt_dialog) -> None:
        selected = self.root / "movie with spaces.mkv"
        run.return_value = subprocess.CompletedProcess([], 0, stdout=str(selected) + "\n", stderr="")
        result = native_dialogs.choose_video(None)
        self.assertEqual(result, str(selected))
        self.assertEqual(run.call_args.args[0][0], "kdialog")
        self.assertIn("--getopenfilename", run.call_args.args[0])
        qt_dialog.assert_not_called()

    @patch("ui.native_dialogs.QFileDialog.getOpenFileName")
    @patch("ui.native_dialogs.subprocess.run")
    @patch("ui.native_dialogs.shutil.which", return_value="/usr/bin/kdialog")
    def test_cancel_does_not_open_a_second_dialog(self, _which, run, qt_dialog) -> None:
        run.return_value = subprocess.CompletedProcess([], 1, stdout="", stderr="")
        self.assertEqual(native_dialogs.choose_video(None), "")
        self.assertEqual(run.call_count, 1)
        qt_dialog.assert_not_called()

    @patch("ui.native_dialogs.QFileDialog.getOpenFileNames")
    @patch("ui.native_dialogs.subprocess.run")
    @patch("ui.native_dialogs.shutil.which", return_value="/usr/bin/kdialog")
    def test_batch_video_picker_accepts_multiple_sources(self, _which, run, qt_dialog) -> None:
        first = self.root / "one.mp4"
        second = self.root / "two.mkv"
        run.return_value = subprocess.CompletedProcess([], 0, stdout=f"{first}\n{second}\n", stderr="")
        result = native_dialogs.choose_videos(None)
        self.assertEqual(result, [str(first), str(second)])
        self.assertIn("--multiple", run.call_args.args[0])
        qt_dialog.assert_not_called()

    @patch("ui.native_dialogs.subprocess.run")
    @patch("ui.native_dialogs.shutil.which", return_value="/usr/bin/tool")
    def test_failed_kdialog_falls_back_to_zenity(self, _which, run) -> None:
        selected = self.root / "export"
        selected.mkdir()
        run.side_effect = [
            subprocess.CompletedProcess([], -6, stdout="", stderr="crash"),
            subprocess.CompletedProcess([], 0, stdout=str(selected) + "\n", stderr=""),
        ]
        result = native_dialogs.choose_directory(None, "Choose export folder")
        self.assertEqual(result, str(selected))
        self.assertEqual(run.call_args_list[0].args[0][0], "kdialog")
        self.assertEqual(run.call_args_list[1].args[0][0], "zenity")
        state = json.loads((self.root / "clipivo" / "dialog_dirs.json").read_text(encoding="utf-8"))
        self.assertEqual(state["export_folder"], str(selected))

    @patch("ui.native_dialogs.QFileDialog.getSaveFileName")
    @patch("ui.native_dialogs.subprocess.run")
    @patch("ui.native_dialogs.shutil.which", return_value="/usr/bin/kdialog")
    def test_single_clip_uses_kde_save_file_dialog(self, _which, run, qt_dialog) -> None:
        selected = self.root / "named clip"
        run.return_value = subprocess.CompletedProcess([], 0, stdout=str(selected) + "\n", stderr="")
        result = native_dialogs.choose_save_file(None, "Save exported video", "suggested.mp4")
        self.assertEqual(result, str(selected.with_suffix(".mp4")))
        self.assertIn("--getsavefilename", run.call_args.args[0])
        self.assertTrue(any(str(argument).endswith("suggested.mp4") for argument in run.call_args.args[0]))
        qt_dialog.assert_not_called()

    @patch("ui.native_dialogs.QFileDialog.getSaveFileName")
    @patch("ui.native_dialogs.subprocess.run")
    @patch("ui.native_dialogs.shutil.which", return_value="/usr/bin/kdialog")
    def test_screenshot_uses_png_native_save_dialog(self, _which, run, qt_dialog) -> None:
        selected = self.root / "frame capture"
        run.return_value = subprocess.CompletedProcess([], 0, stdout=str(selected) + "\n", stderr="")
        result = native_dialogs.choose_png_file(None, "Save frame", "movie-00-00-01-000.png")
        self.assertEqual(result, str(selected.with_suffix(".png")))
        self.assertIn("*.png|PNG image", run.call_args.args[0])
        qt_dialog.assert_not_called()


if __name__ == "__main__":
    unittest.main()
