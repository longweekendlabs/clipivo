"""First-launch downloads: the face models, and ffmpeg where the system has none."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QThread, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout

from core import downloads

from .styles import PALETTE

VLC_URL = "https://www.videolan.org/vlc/"


@dataclass
class DownloadItem:
    name: str
    size: int
    step: Callable
    folder: Path
    links: list[str] = field(default_factory=list)


class DownloadWorker(QThread):
    progressed = Signal(int, int, str)
    succeeded = Signal()
    failed = Signal(str, bool)

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
            self.failed.emit(str(exc), exc.blocked)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}", False)


def missing_downloads() -> list[DownloadItem]:
    """What this computer still needs, with where each goes and links for a manual download."""
    needed = []
    if downloads.find_face_models() is None:
        links, folder = downloads.face_models_manual()
        needed.append(DownloadItem("Face models (InsightFace buffalo_l)", downloads.face_models_download_size(),
                                   downloads.download_face_models, folder, links))
    if not downloads.ffmpeg_available() and downloads.ffmpeg_can_download():
        links, folder = downloads.ffmpeg_manual()
        needed.append(DownloadItem("ffmpeg, for saving clips", downloads.ffmpeg_download_size(),
                                   downloads.download_ffmpeg, folder, links))
    return needed


def _megabytes(size: int) -> str:
    return f"{size / 1_000_000:,.0f} MB" if size < 1_000_000_000 else f"{size / 1_000_000_000:.1f} GB"


class DownloadDialog(QDialog):
    """Lists what will be downloaded and where, then downloads it with progress and Cancel.
    When a server cannot be reached it says so, with the links and folder for doing it by hand."""

    def __init__(self, title: str, intro: str, items: list[DownloadItem], parent=None, *, note: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(560)
        self.items = items
        self.worker: DownloadWorker | None = None
        self._started_at = 0.0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)

        heading = QLabel(intro)
        heading.setWordWrap(True)
        layout.addWidget(heading)
        for item in items:
            row = QLabel(
                f"·  {item.name}  <span style='color:{PALETTE['muted']}'>{_megabytes(item.size)}</span><br>"
                f"<span style='color:{PALETTE['muted']}; font-size:11px'>&nbsp;&nbsp;&nbsp;Saved to {item.folder}</span>"
            )
            row.setTextFormat(Qt.RichText)
            row.setWordWrap(True)
            row.setTextInteractionFlags(Qt.TextSelectableByMouse)
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
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status.setStyleSheet(f"color: {PALETTE['secondary']};")
        layout.addWidget(self.status)

        # Shown when a server cannot be reached: how to get the files another way.
        self.manual = QLabel("")
        self.manual.setWordWrap(True)
        self.manual.setTextFormat(Qt.RichText)
        self.manual.setOpenExternalLinks(True)
        self.manual.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.manual.hide()
        layout.addWidget(self.manual)

        buttons = QHBoxLayout()
        self.folder_button = QPushButton("Open folder")
        self.folder_button.setToolTip("Show where the downloaded files are kept")
        self.folder_button.clicked.connect(self._open_folder)
        self.copy_button = QPushButton("Copy links")
        self.copy_button.clicked.connect(self._copy_links)
        self.copy_button.hide()
        buttons.addWidget(self.folder_button)
        buttons.addWidget(self.copy_button)
        buttons.addStretch()
        self.later_button = QPushButton("Later")
        self.later_button.clicked.connect(self._later)
        self.download_button = QPushButton(f"Download {_megabytes(sum(item.size for item in items))}")
        self.download_button.setDefault(True)
        self.download_button.clicked.connect(self._start)
        buttons.addWidget(self.later_button)
        buttons.addWidget(self.download_button)
        layout.addLayout(buttons)

    def _start(self) -> None:
        self.download_button.setEnabled(False)
        self.later_button.setText("Cancel")
        self.manual.hide()
        self.copy_button.hide()
        self.progress.show()
        self.progress.setValue(0)
        self.status.setStyleSheet(f"color: {PALETTE['secondary']};")
        self.status.setText("Connecting…")
        self._started_at = time.monotonic()
        self.worker = DownloadWorker([item.step for item in self.items], self)
        self.worker.progressed.connect(self._progressed)
        self.worker.succeeded.connect(self._succeeded)
        self.worker.failed.connect(self._failed)
        self.worker.start()

    def _progressed(self, done: int, total: int, label: str) -> None:
        self.progress.setValue(round(done / max(1, total) * 1000))
        elapsed = max(0.5, time.monotonic() - self._started_at)
        speed = done / elapsed
        left = (total - done) / speed if speed > 0 else 0
        self.status.setText(
            f"{label} · {_megabytes(done)} of {_megabytes(total)} · {speed / 1_000_000:.1f} MB/s"
            + (f" · about {int(left // 60)} min {int(left % 60)} s left" if left >= 1 else "")
        )

    def _succeeded(self) -> None:
        self.worker = None
        self.accept()

    def _failed(self, detail: str, blocked: bool) -> None:
        self.worker = None
        self.download_button.setEnabled(True)
        self.download_button.setText("Try again")
        self.later_button.setText("Later")
        self.progress.hide()
        self.status.setStyleSheet(f"color: {PALETTE['accent']};")
        self.status.setText(detail)
        if detail == "Download cancelled.":
            return
        links = [link for item in self.items for link in item.links]
        if links:
            listed = "<br>".join(f"<a href='{link}' style='color:{PALETTE['accent']}'>{link}</a>" for link in links)
            folders = "<br>".join(sorted({str(item.folder) for item in self.items}))
            lead = (
                "If downloads are blocked on this computer, allow Clipivo in your firewall or antivirus, "
                "or download these files in a browser"
                if blocked else "You can also download these files in a browser"
            )
            self.manual.setText(
                f"<span style='color:{PALETTE['secondary']}'>{lead} and put them, unchanged, in:</span><br>"
                f"<span style='color:{PALETTE['text']}'>{folders}</span><br>{listed}<br>"
                f"<span style='color:{PALETTE['muted']}'>Zip files: put the programs inside them in that folder. "
                "Clipivo checks the files when it next starts.</span>"
            )
            self.manual.show()
            self.copy_button.show()

    def _open_folder(self) -> None:
        folder = self.items[0].folder if self.items else downloads.data_dir()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _copy_links(self) -> None:
        lines = [link for item in self.items for link in item.links]
        lines += [f"Folder: {folder}" for folder in sorted({str(item.folder) for item in self.items})]
        QApplication.clipboard().setText("\n".join(lines))
        self.copy_button.setText("Copied")

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
        note=downloads.FACE_MODEL_NOTICE if any("Face models" in item.name for item in items) else "",
    )
    dialog.exec()
    return not missing_downloads()


def open_vlc_page() -> None:
    QDesktopServices.openUrl(QUrl(VLC_URL))


def ffmpeg_missing_message() -> str:
    if sys.platform.startswith("linux"):
        return "Saving clips needs ffmpeg. Install it with your package manager, for example: sudo dnf install ffmpeg"
    return "Saving clips needs ffmpeg."
