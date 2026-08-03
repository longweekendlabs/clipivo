from __future__ import annotations

import os
import sys
from pathlib import Path

# libVLC 3 embeds video in an X11 child window. Fedora's XWayland bridge keeps
# the rest of Clipivo native-looking while providing that reliable zero-copy
# playback surface on Wayland desktops.
if sys.platform.startswith("linux") and os.environ.get("DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from core.character_store import CharacterStore, CharacterStoreError
from core.face_engine import InsightFaceEngine
from ui.main_window import MainWindow
from version import __app_name__, __org_name__, __version__


PROJECT_DIR = Path(__file__).resolve().parent


def load_fonts() -> None:
    for name in ("Inter-Regular.ttf", "Inter-Bold.ttf"):
        QFontDatabase.addApplicationFont(str(PROJECT_DIR / "assets" / "fonts" / name))


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(__app_name__)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(__org_name__)
    app.setStyle("Fusion")
    load_fonts()

    store = None
    store_error = ""
    try:
        store = CharacterStore()
    except CharacterStoreError as exc:
        store_error = str(exc)
    face_engine = InsightFaceEngine(PROJECT_DIR)
    window = MainWindow(PROJECT_DIR, store, face_engine, store_error)
    window.show()
    if len(sys.argv) > 1:
        source = Path(sys.argv[1]).expanduser()
        if source.is_file():
            QTimer.singleShot(0, lambda: window.open_video(source))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
