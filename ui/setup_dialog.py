"""First-launch downloads: the face models, and ffmpeg where the system has none."""

from __future__ import annotations

import sys
from typing import Callable

from PySide6.QtCore import QThread, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout

from core import downloads

from .styles import PALETTE

VLC_URL = "https://www.videolan.org/vlc/"


class DownloadWorker(QThread):
    progressed = Signal(int, int, str)
    succeeded = Signal()
    failed = Signal(str)

    def __init__(self, steps: list[Callable], parent=None):
        super().__init__(parent)
        self.steps = steps
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            for step in self.steps:
                step(lambda done, total, label: self.progressed.emit(done, total, label), lambda: self._cancelled)
            self.succeeded.emit()
        except downloads.DownloadError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")


def missing_downloads() -> list[tuple[str, int, Callable]]:
    """(description, bytes, download step) for what this computer still needs."""
    needed = []
    if downloads.find_face_models() is None:
        needed.append(("Face models (InsightFace buffalo_l)", downloads.face_models_download_size(), downloads.download_face_models))
    if not downloads.ffmpeg_available() and downloads.ffmpeg_can_download():
        needed.append(("ffmpeg, for saving clips", downloads.ffmpeg_download_size(), downloads.download_ffmpeg))
    return needed


def _megabytes(size: int) -> str:
    return f"{size / 1_000_000:,.0f} MB" if size < 1_000_000_000 else f"{size / 1_000_000_000:.1f} GB"


class DownloadDialog(QDialog):
    """Lists what will be downloaded, then downloads it with progress and Cancel."""

    def __init__(self, title: str, intro: str, items: list[tuple[str, int, Callable]], parent=None, *, note: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(480)
        self.items = items
        self.worker: DownloadWorker | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)

        heading = QLabel(intro)
        heading.setWordWrap(True)
        layout.addWidget(heading)
        for name, size, _step in items:
            row = QLabel(f"·  {name}  <span style='color:{PALETTE['muted']}'>{_megabytes(size)}</span>")
            row.setTextFormat(Qt.RichText)
            layout.addWidget(row)
        if note:
            small = QLabel(note)
            small.setWordWrap(True)
            small.setStyleSheet(f"color: {PALETTE['muted']}; font-size: 11px;")
            layout.addWidget(small)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color: {PALETTE['secondary']};")
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.later_button = QPushButton("Later")
        self.later_button.clicked.connect(self._later)
        self.download_button = QPushButton(f"Download {_megabytes(sum(size for _n, size, _s in items))}")
        self.download_button.setDefault(True)
        self.download_button.clicked.connect(self._start)
        buttons.addWidget(self.later_button)
        buttons.addWidget(self.download_button)
        layout.addLayout(buttons)

    def _start(self) -> None:
        self.download_button.setEnabled(False)
        self.later_button.setText("Cancel")
        self.progress.show()
        self.progress.setValue(0)
        self.status.setText("Starting…")
        self.worker = DownloadWorker([step for _name, _size, step in self.items], self)
        self.worker.progressed.connect(self._progressed)
        self.worker.succeeded.connect(self._succeeded)
        self.worker.failed.connect(self._failed)
        self.worker.start()

    def _progressed(self, done: int, total: int, label: str) -> None:
        self.progress.setValue(round(done / max(1, total) * 1000))
        self.status.setText(f"{label} · {_megabytes(done)} of {_megabytes(total)}")

    def _succeeded(self) -> None:
        self.worker = None
        self.accept()

    def _failed(self, detail: str) -> None:
        self.worker = None
        self.download_button.setEnabled(True)
        self.download_button.setText("Try again")
        self.later_button.setText("Later")
        self.progress.hide()
        self.status.setText(detail)

    def _later(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self.status.setText("Cancelling…")
            self.worker.wait()
            self.worker = None
        self.reject()

    def closeEvent(self, event) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self.worker.wait()
        super().closeEvent(event)


def offer_downloads(parent) -> bool:
    """Ask for anything still missing. True when everything needed is now present."""
    items = missing_downloads()
    if not items:
        return True
    if QApplication.platformName() == "offscreen":
        # Nobody can answer a dialog here (tests, --self-test); report instead of waiting.
        return False
    dialog = DownloadDialog(
        "Finish setting up Clipivo",
        "Clipivo downloads these once, instead of carrying them in its installer:",
        items,
        parent,
        note=downloads.FACE_MODEL_NOTICE if any("Face models" in name for name, _s, _d in items) else "",
    )
    dialog.exec()
    return not missing_downloads()


def open_vlc_page() -> None:
    QDesktopServices.openUrl(QUrl(VLC_URL))


def ffmpeg_missing_message() -> str:
    if sys.platform.startswith("linux"):
        return "Saving clips needs ffmpeg. Install it with your package manager, for example: sudo dnf install ffmpeg"
    return "Saving clips needs ffmpeg."
