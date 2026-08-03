from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QWidget


VIDEO_EXTENSIONS = ("*.mp4", "*.mkv", "*.webm", "*.mov", "*.avi", "*.m4v")
VIDEO_FILTER_QT = "Video files (*.mp4 *.mkv *.webm *.mov *.avi *.m4v);;All files (*)"
VIDEO_FILTER_KDE = "*.mp4 *.mkv *.webm *.mov *.avi *.m4v|Video files"
IMAGE_FILTER_QT = "Images (*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff);;All files (*)"
IMAGE_FILTER_KDE = "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff|Images"
MP4_FILTER_QT = "MP4 video (*.mp4)"
MP4_FILTER_KDE = "*.mp4|MP4 video"
PNG_FILTER_QT = "PNG image (*.png)"
PNG_FILTER_KDE = "*.png|PNG image"
GEOMETRY = "1000x680"


def choose_video(parent: QWidget, title: str = "Open video", key: str = "video_source") -> str:
    start = _start_directory(key)
    for backend in _linux_backend_order():
        if backend == "kdialog":
            result = _kdialog_file(title, start)
        else:
            result = _zenity_file(title, start)
        if result is not None:
            if result:
                _remember(key, str(Path(result).parent))
            return result

    result, _selected_filter = QFileDialog.getOpenFileName(parent, title, start, VIDEO_FILTER_QT)
    if result:
        _remember(key, str(Path(result).parent))
    return result


def choose_videos(parent: QWidget, title: str = "Add videos", key: str = "batch_sources") -> list[str]:
    start = _start_directory(key)
    for backend in _linux_backend_order():
        if backend == "kdialog":
            result = _run_dialog([
                "kdialog", "--geometry", GEOMETRY, "--title", title,
                "--multiple", "--separate-output", "--getopenfilename", start, VIDEO_FILTER_KDE,
            ])
        else:
            result = _run_dialog([
                "zenity", "--file-selection", "--multiple", "--separator=\n",
                "--title", title, "--filename", start.rstrip("/") + "/",
                "--file-filter", "Video files | *.mp4 *.mkv *.webm *.mov *.avi *.m4v",
                "--file-filter", "All files | *",
            ])
        if result is not None:
            paths = [line for line in result.splitlines() if line]
            if paths:
                _remember(key, str(Path(paths[0]).parent))
            return paths

    results, _selected_filter = QFileDialog.getOpenFileNames(parent, title, start, VIDEO_FILTER_QT)
    if results:
        _remember(key, str(Path(results[0]).parent))
    return results


def choose_images(parent: QWidget, title: str = "Add reference photos", key: str = "reference_photos") -> list[str]:
    start = _start_directory(key)
    for backend in _linux_backend_order():
        if backend == "kdialog":
            result = _run_dialog([
                "kdialog", "--geometry", GEOMETRY, "--title", title,
                "--multiple", "--separate-output", "--getopenfilename", start, IMAGE_FILTER_KDE,
            ])
        else:
            result = _run_dialog([
                "zenity", "--file-selection", "--multiple", "--separator=\n",
                "--title", title, "--filename", start.rstrip("/") + "/",
                "--file-filter", "Images | *.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff",
                "--file-filter", "All files | *",
            ])
        if result is not None:
            paths = [line for line in result.splitlines() if line]
            if paths:
                _remember(key, str(Path(paths[0]).parent))
            return paths

    results, _selected_filter = QFileDialog.getOpenFileNames(parent, title, start, IMAGE_FILTER_QT)
    if results:
        _remember(key, str(Path(results[0]).parent))
    return results


def choose_directory(
    parent: QWidget,
    title: str,
    key: str = "export_folder",
    preferred_start: Path | None = None,
) -> str:
    start = _start_directory(key, preferred_start)
    for backend in _linux_backend_order():
        if backend == "kdialog":
            result = _kdialog_directory(title, start)
        else:
            result = _zenity_directory(title, start)
        if result is not None:
            if result:
                _remember(key, result)
            return result

    result = QFileDialog.getExistingDirectory(parent, title, start)
    if result:
        _remember(key, result)
    return result


def choose_save_file(
    parent: QWidget,
    title: str,
    suggested_name: str,
    key: str = "export_folder",
    preferred_start: Path | None = None,
) -> str:
    start = Path(_start_directory(key, preferred_start)) / suggested_name
    for backend in _linux_backend_order():
        if backend == "kdialog":
            result = _kdialog_save_file(title, str(start))
        else:
            result = _zenity_save_file(title, str(start))
        if result is not None:
            if result:
                result = _ensure_mp4_suffix(result)
                _remember(key, str(Path(result).parent))
            return result

    result, _selected_filter = QFileDialog.getSaveFileName(parent, title, str(start), MP4_FILTER_QT)
    if result:
        result = _ensure_mp4_suffix(result)
        _remember(key, str(Path(result).parent))
    return result


def choose_png_file(
    parent: QWidget,
    title: str,
    suggested_name: str,
    key: str = "screenshot_folder",
    preferred_start: Path | None = None,
) -> str:
    start = Path(_start_directory(key, preferred_start)) / suggested_name
    for backend in _linux_backend_order():
        if backend == "kdialog":
            result = _run_dialog([
                "kdialog", "--geometry", GEOMETRY, "--title", title,
                "--getsavefilename", str(start), PNG_FILTER_KDE,
            ])
        else:
            result = _run_dialog([
                "zenity", "--file-selection", "--save", "--confirm-overwrite",
                "--title", title, "--filename", str(start),
                "--file-filter", "PNG image | *.png",
            ])
        if result is not None:
            if result:
                result = _ensure_png_suffix(result)
                _remember(key, str(Path(result).parent))
            return result

    result, _selected_filter = QFileDialog.getSaveFileName(parent, title, str(start), PNG_FILTER_QT)
    if result:
        result = _ensure_png_suffix(result)
        _remember(key, str(Path(result).parent))
    return result


def _linux_backend_order() -> tuple[str, ...]:
    if not sys.platform.startswith("linux"):
        return ()
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
    preferred = ("kdialog", "zenity") if "KDE" in desktop or "PLASMA" in desktop else ("zenity", "kdialog")
    return tuple(name for name in preferred if shutil.which(name))


def _kdialog_file(title: str, start: str) -> str | None:
    return _run_dialog([
        "kdialog", "--geometry", GEOMETRY, "--title", title,
        "--getopenfilename", start, VIDEO_FILTER_KDE,
    ])


def _kdialog_directory(title: str, start: str) -> str | None:
    return _run_dialog([
        "kdialog", "--geometry", GEOMETRY, "--title", title,
        "--getexistingdirectory", start,
    ])


def _kdialog_save_file(title: str, suggested: str) -> str | None:
    return _run_dialog([
        "kdialog", "--geometry", GEOMETRY, "--title", title,
        "--getsavefilename", suggested, MP4_FILTER_KDE,
    ])


def _zenity_file(title: str, start: str) -> str | None:
    return _run_dialog([
        "zenity", "--file-selection", "--title", title,
        "--filename", start.rstrip("/") + "/",
        "--file-filter", "Video files | *.mp4 *.mkv *.webm *.mov *.avi *.m4v",
        "--file-filter", "All files | *",
    ])


def _zenity_directory(title: str, start: str) -> str | None:
    return _run_dialog([
        "zenity", "--file-selection", "--directory", "--title", title,
        "--filename", start.rstrip("/") + "/",
    ])


def _zenity_save_file(title: str, suggested: str) -> str | None:
    return _run_dialog([
        "zenity", "--file-selection", "--save", "--confirm-overwrite",
        "--title", title, "--filename", suggested,
        "--file-filter", "MP4 video | *.mp4",
    ])


def _run_dialog(command: list[str]) -> str | None:
    """Return a path, an empty string for user cancellation, or None on failure."""
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError:
        return None
    if result.returncode == 0:
        return result.stdout.strip()
    if result.returncode == 1:
        return ""
    return None


def _start_directory(key: str, preferred: Path | None = None) -> str:
    remembered = _load_state().get(key)
    if remembered and Path(remembered).is_dir():
        return remembered
    if preferred is not None and Path(preferred).is_dir():
        return str(Path(preferred))
    return str(Path.home())


def _remember(key: str, folder: str) -> None:
    if not key or not folder:
        return
    state = _load_state()
    state[key] = folder
    target = _state_file()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
        temporary.replace(target)
    except OSError:
        pass


def _load_state() -> dict[str, str]:
    try:
        payload = json.loads(_state_file().read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _state_file() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "clipivo" / "dialog_dirs.json"


def _ensure_mp4_suffix(filename: str) -> str:
    path = Path(filename)
    return str(path if path.suffix.lower() == ".mp4" else path.with_suffix(".mp4"))


def _ensure_png_suffix(filename: str) -> str:
    path = Path(filename)
    return str(path if path.suffix.lower() == ".png" else path.with_suffix(".png"))
