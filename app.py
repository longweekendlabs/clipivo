from __future__ import annotations

import os
import sys
from pathlib import Path

# Variables Clipivo, its packaging or its libraries change for this process, which programs
# it starts must not inherit. A packaged Linux build points LD_LIBRARY_PATH at its own Qt,
# GLib, OpenSSL and libstdc++; PySide6 sets QT_PLUGIN_PATH to its plugins; OpenCV sets
# LD_LIBRARY_PATH again when imported. kdialog, zenity, ffmpeg and xdg-open (Open folder,
# links) then load those libraries instead of their own and fail to start. The user's values
# are recorded here, before anything changes them, and restored once everything has loaded.
_INHERITED = ("LD_LIBRARY_PATH", "QT_PLUGIN_PATH", "QML2_IMPORT_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
              "QT_QPA_PLATFORM", "RESOURCE_NAME")
_USER_ENVIRONMENT = {key: os.environ.get(key) for key in _INHERITED}
if getattr(sys, "frozen", False):
    # The bundle's own path is not the user's; PyInstaller keeps theirs here, if they had one.
    _USER_ENVIRONMENT["LD_LIBRARY_PATH"] = os.environ.get("LD_LIBRARY_PATH_ORIG")

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


def restore_user_environment(app: QApplication) -> None:
    """Give programs Clipivo starts the user's own environment back. Call after the
    QApplication exists and OpenCV has been imported (it sets LD_LIBRARY_PATH on import)."""
    import cv2  # noqa: F401 - imported now so a later first import cannot change the environment again

    # Keep Qt finding its plugins without the variable: pin the paths it worked out.
    app.setLibraryPaths(app.libraryPaths())
    bundle = str(Path(getattr(sys, "_MEIPASS", APP_DIR)).resolve())
    for key, value in _USER_ENVIRONMENT.items():
        # Packaging hooks run before this file, so a "user" value may already be the bundle's:
        # keep only the entries that are not inside Clipivo's own folder.
        if value and (key.endswith("PATH") or os.pathsep in value):
            kept = [entry for entry in value.split(os.pathsep)
                    if entry and not str(Path(entry).resolve()).startswith(bundle)]
            value = os.pathsep.join(kept) or None
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    os.environ.pop("LD_LIBRARY_PATH_ORIG", None)


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
    app = QApplication.instance() or QApplication(["clipivo", "-platform", "offscreen"])
    restore_user_environment(app)
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
    # Run what Clipivo launches, with the environment it launches them with: a packaged
    # build once broke every external program through its own library path.
    import shutil
    import subprocess
    import tempfile

    launched = {}
    tools = []
    if downloads.ffmpeg_available():
        tools += [("ffmpeg", downloads.tool_path("ffmpeg")), ("ffprobe", downloads.tool_path("ffprobe"))]
    if sys.platform.startswith("linux"):
        tools += [(name, shutil.which(name)) for name in ("kdialog", "zenity", "xdg-open") if shutil.which(name)]
    for name, path in tools:
        try:
            done = subprocess.run([path, "-version" if name in ("ffmpeg", "ffprobe") else "--version"],
                                  capture_output=True, text=True, timeout=30, check=False)
            launched[name] = "ok" if done.returncode == 0 else (
                (done.stderr.strip().splitlines() or done.stdout.strip().splitlines() or [f"exit {done.returncode}"])[0]
            )
        except (OSError, subprocess.SubprocessError) as exc:
            launched[name] = f"{type(exc).__name__}: {exc}"
        if launched[name] != "ok":
            failures.append(f"{name} does not start from Clipivo: {launched[name]}")
    report["launched"] = launched
    # Saved people's key lives in the OS keyring: name the backend without reading anything,
    # so no password prompt appears.
    try:
        import keyring

        backend = keyring.get_keyring()
        report["keyring"] = f"{type(backend).__module__}.{type(backend).__name__}"
        if "fail" in report["keyring"].lower() or "null" in report["keyring"].lower():
            report["keyring"] += " · no usable keyring: saved people cannot be opened"
    except Exception as exc:  # noqa: BLE001
        report["keyring"] = f"{type(exc).__name__}: {exc}"
        failures.append(f"keyring: {exc}")
    report["child_environment"] = {
        key: value for key, value in os.environ.items()
        if key.startswith(("LD_", "QT_", "PYTHON", "GTK", "GDK", "GIO", "GST", "SSL_", "DYLD_"))
    }
    if launched.get("ffmpeg") == "ok" and launched.get("ffprobe") == "ok":
        # A real save: make a short video, read it, cut and re-encode a clip from it.
        try:
            from core.export import OutputShape, export_ranges
            from core.models import ClipRange
            from core.video_probe import probe_video

            with tempfile.TemporaryDirectory(prefix="clipivo-self-test-") as folder:
                source = Path(folder) / "source.mp4"
                subprocess.run([downloads.tool_path("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
                                "testsrc2=size=320x180:rate=25", "-t", "2", "-pix_fmt", "yuv420p", str(source)],
                               check=True, capture_output=True, timeout=60)
                info = probe_video(source)
                import av

                with av.open(str(source)) as container:
                    frame = next(container.decode(video=0))
                    report["decode"] = f"ok · {frame.width}x{frame.height}"
                output, = export_ranges(source, [ClipRange(200, 1500)], Path(folder) / "out", "accurate", False,
                                        shape=OutputShape(90, 0, "whole", (info.width, info.height)))
                saved = probe_video(output)
                report["save"] = f"ok · {saved.video_codec} {saved.width}x{saved.height} {saved.duration_ms} ms"
        except Exception as exc:  # noqa: BLE001
            report["save"] = f"{type(exc).__name__}: {exc}"
            failures.append(f"saving a clip: {exc}")
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
        report["license_bundled"] = (APP_DIR / "LICENSE").is_file()
        # Qt's image plugins must still load after the environment is handed back.
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        from PySide6.QtGui import QImage

        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.WriteOnly)
        QImage(8, 8, QImage.Format_RGB32).save(buffer, "JPG")
        jpeg_ok = not QImage.fromData(data, "JPG").isNull()
        svg_ok = not QImage(str(APP_DIR / "assets" / "clipivo.svg")).isNull()
        report["qt_image_plugins"] = {"jpeg": jpeg_ok, "svg": svg_ok}
        if not (jpeg_ok and svg_ok):
            failures.append(f"Qt image plugins: jpeg {jpeg_ok}, svg {svg_ok}")
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
    restore_user_environment(app)
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
