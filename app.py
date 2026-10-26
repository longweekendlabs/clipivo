from __future__ import annotations

import os
import sys
from pathlib import Path

# libVLC 3 embeds video in an X11 child window. Fedora's XWayland bridge keeps
# the rest of Clipivo native-looking while providing that reliable zero-copy
# playback surface on Wayland desktops.
if sys.platform.startswith("linux") and os.environ.get("DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
# Without this the X11 window class is "python", so the desktop shows a generic icon and name.
os.environ.setdefault("RESOURCE_NAME", "Clipivo")

# The optional Linux GPU download replaces the built-in ONNX Runtime, so it has to be on the
# import path before anything imports onnxruntime.
from core.downloads import activate_gpu_pack  # noqa: E402

activate_gpu_pack()

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QFontDatabase, QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from core.character_store import CharacterStore, CharacterStoreError  # noqa: E402
from core.downloads import APP_DIR, ffmpeg_available  # noqa: E402
from core.export import clean_stale_work  # noqa: E402
from core.face_engine import InsightFaceEngine  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from ui.setup_dialog import ffmpeg_missing_message, missing_downloads, offer_downloads, open_vlc_page  # noqa: E402
from version import __app_name__, __org_name__, __version__  # noqa: E402


# The app's own files: beside this script from source, inside the bundle when packaged.
PROJECT_DIR = APP_DIR


def first_launch_checks(window: MainWindow) -> None:
    """Show what Clipivo found and fetch what is missing (the face models, ffmpeg where needed).
    The setup window lists VLC and ffmpeg too; without it, say if either is missing."""
    if missing_downloads():
        offer_downloads(window, window.face_engine, window.playback_info)
        return
    if not ffmpeg_available():
        QMessageBox.warning(window, "ffmpeg is missing", ffmpeg_missing_message())
    reason = window.playback_unavailable_reason()
    if reason:
        box = QMessageBox(QMessageBox.Information, "Install VLC for playback", reason, QMessageBox.Ok, window)
        box.addButton("Get VLC", QMessageBox.ActionRole).clicked.connect(open_vlc_page)
        box.exec()


def load_fonts() -> None:
    for name in ("Inter-Regular.ttf", "Inter-Bold.ttf"):
        QFontDatabase.addApplicationFont(str(PROJECT_DIR / "assets" / "fonts" / name))


def self_test() -> int:
    """Check a packaged build without opening a window: the libraries load, ONNX Runtime
    runs, and what is still to be downloaded. Prints JSON; exit code 1 on a broken build."""
    import json

    import numpy as np

    from core import downloads
    from core.face_models import FaceAnalysis, execution_providers

    report: dict = {"version": __version__, "platform": sys.platform, "app_dir": str(APP_DIR)}
    failures = []
    try:
        import av
        import cv2

        report["pyav"], report["opencv"] = av.__version__, cv2.__version__
    except Exception as exc:  # noqa: BLE001
        failures.append(f"media libraries: {exc}")
    try:
        import onnxruntime

        report["onnxruntime"] = onnxruntime.__version__
        report["providers"] = execution_providers()
    except Exception as exc:  # noqa: BLE001
        failures.append(f"onnxruntime: {exc}")
    try:
        from core import vlc_locate

        location = vlc_locate.prepare()
        report["vlc_search"] = str(location.library) if location.usable else location.problem
        import vlc

        report["vlc_library"] = getattr(getattr(vlc, "dll", None), "_name", "")
        instance = vlc.Instance("--quiet")
        report["vlc"] = vlc.libvlc_get_version().decode() if instance else "library found, but it did not start"
    except Exception as exc:  # noqa: BLE001
        report["vlc"] = f"not found ({type(exc).__name__})"
    report["ffmpeg"] = downloads.tool_path("ffmpeg") if downloads.ffmpeg_available() else "not found"
    report["ffmpeg_downloadable"] = downloads.ffmpeg_can_download()
    folder = downloads.find_face_models()
    report["face_models"] = str(folder) if folder else "not downloaded"
    if folder:
        try:
            analysis = FaceAnalysis(folder / "det_10g.onnx", folder / "w600k_r50.onnx", 640, execution_providers())
            analysis.get(np.zeros((360, 640, 3), dtype=np.uint8))
            report["face_models_run"] = analysis.providers[0]
        except Exception as exc:  # noqa: BLE001
            failures.append(f"face models: {exc}")
    try:
        app = QApplication.instance() or QApplication(["clipivo", "-platform", "offscreen"])  # noqa: F841
        report["license_bundled"] = (APP_DIR / "LICENSE").is_file()
        report["icon_bundled"] = (APP_DIR / "assets" / "clipivo.svg").is_file()
    except Exception as exc:  # noqa: BLE001
        failures.append(f"Qt: {exc}")
    report["failures"] = failures
    text = json.dumps(report, indent=2)
    # A windowed Windows build has no console; the release workflow reads this file instead.
    target = os.environ.get("CLIPIVO_SELF_TEST_FILE")
    if target:
        Path(target).write_text(text, encoding="utf-8")
    if sys.stdout is not None:
        print(text)
    return 1 if failures else 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    app = QApplication(sys.argv)
    app.setApplicationName(__app_name__)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(__org_name__)
    app.setStyle("Fusion")
    app.setDesktopFileName("io.longweekendlabs.Clipivo")
    app.setWindowIcon(QIcon.fromTheme("io.longweekendlabs.Clipivo", QIcon(str(PROJECT_DIR / "assets" / "clipivo.svg"))))
    load_fonts()
    try:
        clean_stale_work()
    except OSError:
        pass

    store = None
    store_error = ""
    try:
        store = CharacterStore()
    except CharacterStoreError as exc:
        store_error = str(exc)
    face_engine = InsightFaceEngine()
    window = MainWindow(PROJECT_DIR, store, face_engine, store_error)
    window.show()
    QTimer.singleShot(0, lambda: first_launch_checks(window))
    if len(sys.argv) > 1:
        source = Path(sys.argv[1]).expanduser()
        if source.is_file():
            QTimer.singleShot(0, lambda: window.open_video(source))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
