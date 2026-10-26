"""What Clipivo uses, where each piece is, and downloading what is missing.

The Components panel (Settings, and the first-launch window) lists the face models, ffmpeg,
VLC and the face-matching device: found or not, where, shared with which app, and what to do
when something is missing. Downloads show their folder, progress and speed, and say plainly
when a server cannot be reached, with links and the folder for a manual download.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QThread, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core import downloads, vlc_locate

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


def _muted(text: str) -> str:
    return f"<span style='color:{PALETTE['muted']}'>{text}</span>"


class DownloadBox(QWidget):
    """Progress, speed, Cancel, and what to do when a download is blocked, for a set of items."""

    finished = Signal(bool)

    def __init__(self, items: Callable[[], list[DownloadItem]], parent=None):
        super().__init__(parent)
        self._items = items
        self._current: list[DownloadItem] = []
        self.worker: DownloadWorker | None = None
        self._started_at = 0.0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status.setStyleSheet(f"color: {PALETTE['secondary']};")
        layout.addWidget(self.status)
        self.manual = QLabel("")
        self.manual.setWordWrap(True)
        self.manual.setTextFormat(Qt.RichText)
        self.manual.setOpenExternalLinks(True)
        self.manual.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.manual.hide()
        layout.addWidget(self.manual)
        buttons = QHBoxLayout()
        self.folder_button = QPushButton("Open folder")
        self.folder_button.setToolTip("Show where Clipivo keeps downloaded files")
        self.folder_button.clicked.connect(self._open_folder)
        self.copy_button = QPushButton("Copy links")
        self.copy_button.clicked.connect(self._copy_links)
        self.copy_button.hide()
        self.download_button = QPushButton()
        self.download_button.setDefault(True)
        self.download_button.clicked.connect(self._toggle)
        buttons.addWidget(self.folder_button)
        buttons.addWidget(self.copy_button)
        buttons.addStretch()
        buttons.addWidget(self.download_button)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self) -> None:
        if self.worker is not None:
            return
        pending = self._items()
        self.download_button.setVisible(bool(pending))
        self.download_button.setEnabled(bool(pending))
        self.download_button.setText(
            f"Download {_megabytes(sum(item.size for item in pending))}" if pending else "Nothing to download"
        )

    def busy(self) -> bool:
        return self.worker is not None

    def _toggle(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self.status.setText("Cancelling…")
            return
        self._current = self._items()
        if not self._current:
            return
        self.download_button.setText("Cancel")
        self.manual.hide()
        self.copy_button.hide()
        self.progress.show()
        self.progress.setValue(0)
        self.status.setStyleSheet(f"color: {PALETTE['secondary']};")
        self.status.setText("Connecting…")
        self._started_at = time.monotonic()
        self.worker = DownloadWorker([item.step for item in self._current], self)
        self.worker.progressed.connect(self._progressed)
        self.worker.succeeded.connect(self._succeeded)
        self.worker.failed.connect(self._failed)
        self.worker.start()

    def _progressed(self, done: int, total: int, label: str) -> None:
        self.progress.setValue(round(done / max(1, total) * 1000))
        speed = done / max(0.5, time.monotonic() - self._started_at)
        left = (total - done) / speed if speed > 0 else 0
        self.status.setText(
            f"{label} · {_megabytes(done)} of {_megabytes(total)} · {speed / 1_000_000:.1f} MB/s"
            + (f" · about {int(left // 60)} min {int(left % 60)} s left" if left >= 1 else "")
        )

    def _succeeded(self) -> None:
        self.worker = None
        self.progress.hide()
        self.status.setStyleSheet(f"color: {PALETTE['secondary']};")
        self.status.setText("Downloaded and checked.")
        self.refresh()
        self.finished.emit(True)

    def _failed(self, detail: str, blocked: bool) -> None:
        self.worker = None
        self.progress.hide()
        self.status.setStyleSheet(f"color: {PALETTE['accent']};")
        self.status.setText(detail)
        self.refresh()
        if self.download_button.isEnabled():
            self.download_button.setText("Try again")
        self.finished.emit(False)
        if detail == "Download cancelled.":
            return
        links = [link for item in self._current for link in item.links]
        if links:
            listed = "<br>".join(f"<a href='{link}' style='color:{PALETTE['accent']}'>{link}</a>" for link in links)
            folders = "<br>".join(sorted({str(item.folder) for item in self._current}))
            lead = (
                "If downloads are blocked on this computer, allow Clipivo in your firewall or antivirus, "
                "or download these files in a browser"
                if blocked else "You can also download these files in a browser"
            )
            self.manual.setText(
                f"<span style='color:{PALETTE['secondary']}'>{lead} and put them, unchanged, in:</span><br>"
                f"<span style='color:{PALETTE['text']}'>{folders}</span><br>{listed}<br>"
                + _muted("Zip files: put the programs inside them in that folder. Clipivo checks the files when it next looks.")
            )
            self.manual.show()
            self.copy_button.show()

    def _open_folder(self) -> None:
        folder = self._current[0].folder if self._current else downloads.data_dir()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _copy_links(self) -> None:
        lines = [link for item in self._current for link in item.links]
        lines += [f"Folder: {folder}" for folder in sorted({str(item.folder) for item in self._current})]
        QApplication.clipboard().setText("\n".join(lines))
        self.copy_button.setText("Copied")

    def stop(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self.worker.wait()
            self.worker = None


class DownloadDialog(QDialog):
    """A one-off download (the optional GPU pack): what, where, then the download box."""

    def __init__(self, title: str, intro: str, items: list[DownloadItem], parent=None, *, note: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)
        heading = QLabel(intro)
        heading.setWordWrap(True)
        layout.addWidget(heading)
        for item in items:
            row = QLabel(f"·  {item.name}  {_muted(_megabytes(item.size))}<br>"
                         f"<span style='color:{PALETTE['muted']}; font-size:11px'>&nbsp;&nbsp;&nbsp;Saved to {item.folder}</span>")
            row.setTextFormat(Qt.RichText)
            row.setWordWrap(True)
            row.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(row)
        if note:
            small = QLabel(note)
            small.setWordWrap(True)
            small.setStyleSheet(f"color: {PALETTE['muted']}; font-size: 11px;")
            layout.addWidget(small)
        remaining = list(items)
        self.box = DownloadBox(lambda: remaining, self)
        self.box.finished.connect(lambda ok: (remaining.clear(), self.accept()) if ok else None)
        layout.addWidget(self.box)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        layout.addWidget(close, 0, Qt.AlignRight)

    def reject(self) -> None:
        self.box.stop()
        super().reject()


# What Clipivo uses ------------------------------------------------------------------------

def ffmpeg_status() -> tuple[bool, str]:
    """(present, description) for ffmpeg: version and where it is."""
    if not downloads.ffmpeg_available():
        if downloads.ffmpeg_can_download():
            return False, f"not downloaded yet, {_megabytes(downloads.ffmpeg_download_size())} · goes to {downloads.tools_dir()}"
        return False, "not installed · install it with your package manager, for example: sudo dnf install ffmpeg"
    path = downloads.tool_path("ffmpeg")
    version = ""
    try:
        first = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=10, check=False).stdout.split("\n")[0]
        version = first.split(" ")[2] if first.startswith("ffmpeg version") else ""
    except (OSError, subprocess.SubprocessError, IndexError):
        pass
    source = "downloaded by Clipivo" if Path(path).parent == downloads.tools_dir() else "from the system"
    return True, f"{version + ' · ' if version else ''}{source} · {path}"


def _nvidia_name() -> str:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return ""
    try:
        out = subprocess.run([executable, "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True, timeout=5, check=False)
        return out.stdout.strip().splitlines()[0] if out.returncode == 0 and out.stdout.strip() else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def device_status(engine) -> tuple[bool | None, str]:
    """(mark, description) for face matching: True on a GPU, None on the CPU while the GPU
    could be used (an action is offered), True on the CPU when nothing faster is possible."""
    names = {"CUDAExecutionProvider": "CUDA", "DmlExecutionProvider": "DirectML", "CoreMLExecutionProvider": "Core ML"}
    try:
        from core.face_models import execution_providers

        available = execution_providers()
    except Exception as exc:  # noqa: BLE001
        return False, f"ONNX Runtime did not load: {exc}"
    application = getattr(engine, "_application", None) if engine else None
    active = application.providers[0] if application is not None else None
    wants_gpu = getattr(engine, "use_gpu", True)
    chosen = active or (available[0] if wants_gpu else "CPUExecutionProvider")
    card = _nvidia_name()
    if chosen != "CPUExecutionProvider":
        label = names.get(chosen, chosen)
        extra = " · CUDA from Clipivo's download" if chosen == "CUDAExecutionProvider" and downloads.gpu_pack_installed() else ""
        device = f" ({card})" if chosen == "CUDAExecutionProvider" and card else ""
        return True, f"On the GPU through {label}{device}{extra}"
    if not wants_gpu and available[0] != "CPUExecutionProvider":
        return None, "On the CPU because “Use the GPU when available” is off in Local face engine"
    if downloads.gpu_pack_installed():
        return None, "NVIDIA's CUDA libraries are downloaded · restart Clipivo to use the GPU"
    if downloads.gpu_pack_offered():
        return None, (f"On the CPU · it works, but {card or 'your NVIDIA card'} would make scans and following a "
                      "person several times faster with NVIDIA's CUDA libraries, a one-time download")
    return True, "On the CPU · this computer has no graphics card Clipivo can use"


class ComponentsPanel(QWidget):
    """Each piece Clipivo relies on, found or not, and where; downloads for what is missing."""

    def __init__(self, engine=None, playback: Callable[[], tuple[str, str, str]] | None = None, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.playback = playback
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.rows: dict[str, QLabel] = {}
        for key in ("models", "ffmpeg", "vlc", "device"):
            row = QLabel()
            row.setWordWrap(True)
            row.setTextFormat(Qt.RichText)
            row.setOpenExternalLinks(True)
            row.setTextInteractionFlags(Qt.TextBrowserInteraction)
            self.rows[key] = row
            if key == "device":
                # The GPU action sits on the row it changes.
                self.gpu_button = QPushButton()
                self.gpu_button.clicked.connect(self._gpu_clicked)
                line = QHBoxLayout()
                line.addWidget(row, 1)
                line.addWidget(self.gpu_button, 0, Qt.AlignVCenter)
                layout.addLayout(line)
            else:
                layout.addWidget(row)
        self.where = QLabel()
        self.where.setWordWrap(True)
        self.where.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.where.setStyleSheet(f"color: {PALETTE['muted']}; font-size: 11px;")
        layout.addWidget(self.where)
        self.box = DownloadBox(missing_downloads, self)
        self.box.finished.connect(lambda _ok: self.refresh())
        layout.addWidget(self.box)
        check = QPushButton("Look again")
        check.setToolTip("Search this computer again, for example after installing VLC or copying files in")
        check.clicked.connect(self.look_again)
        self.box.layout().itemAt(self.box.layout().count() - 1).layout().insertWidget(2, check)
        self.refresh()

    def _line(self, ok: bool | None, title: str, detail: str) -> str:
        mark, colour = ("✓", PALETTE["accent"]) if ok else (("✗", PALETTE["accent"]) if ok is False else ("○", PALETTE["muted"]))
        return (f"<span style='color:{colour}'>{mark}</span>&nbsp; <span style='color:{PALETTE['text']}'>{title}</span>"
                f"<br>&nbsp;&nbsp;&nbsp;&nbsp;{_muted(detail)}")

    def look_again(self) -> None:
        downloads.forget_found()
        self.refresh()

    def refresh(self) -> None:
        folder = downloads.find_face_models()
        if folder:
            models = self._line(True, "Face models · InsightFace buffalo_l", f"{downloads.describe_location(folder)} · {folder}")
        else:
            models = self._line(None, "Face models · InsightFace buffalo_l",
                                f"not found on this computer · {_megabytes(downloads.face_models_download_size())} to download · goes to {downloads.face_model_dir()}")
        self.rows["models"].setText(models)
        present, detail = ffmpeg_status()
        self.rows["ffmpeg"].setText(self._line(True if present else (None if downloads.ffmpeg_can_download() else False), "ffmpeg · saves clips", detail))
        library, version, problem = self.playback() if self.playback else ("", "", "")
        location = vlc_locate.locate()
        if version:
            vlc = self._line(True, "VLC · plays video", f"{version} · {library or location.library}")
        else:
            reason = problem or location.problem or "not started yet"
            vlc = self._line(False, "VLC · plays video",
                             f"{reason} · Clipivo uses the VLC installed on this computer and never installs its own · "
                             f"<a href='{VLC_URL}' style='color:{PALETTE['accent']}'>Get VLC</a>")
        self.rows["vlc"].setText(vlc)
        mark, detail = device_status(self.engine)
        self.rows["device"].setText(self._line(mark, "Face matching", detail))
        installed = downloads.gpu_pack_installed()
        self.gpu_button.setVisible(installed or downloads.gpu_pack_offered())
        self.gpu_button.setText("Remove CUDA download" if installed else "Use NVIDIA GPU · 1.5 GB")
        self.where.setText(f"Clipivo keeps what it downloads in {downloads.data_dir()}. "
                           "A copy already on this computer, from another app, is used instead of downloading again.")
        self.box.refresh()


    def _gpu_clicked(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        if downloads.gpu_pack_installed():
            downloads.remove_gpu_pack()
            QMessageBox.information(self, "CUDA download removed", "Restart Clipivo to go back to the CPU.")
            self.refresh()
            return
        self.gpu_button.setEnabled(False)
        self.gpu_button.setText("Looking it up…")
        QApplication.processEvents()
        try:
            plan = downloads.gpu_pack_plan()
        except downloads.DownloadError as exc:
            QMessageBox.warning(self, "GPU acceleration", str(exc))
            return
        finally:
            self.gpu_button.setEnabled(True)
            self.refresh()
        item = DownloadItem(
            "ONNX Runtime for CUDA and NVIDIA's CUDA libraries", downloads.gpu_pack_size(plan),
            lambda progress, cancelled: downloads.download_gpu_pack(plan, progress, cancelled),
            downloads.gpu_dir(), [entry.urls[0] for entry in plan],
        )
        dialog = DownloadDialog(
            "Use the NVIDIA GPU", "Clipivo downloads these from PyPI and keeps them in its own folder:", [item], self,
            note="Your NVIDIA driver must support CUDA 13. If it does not, Clipivo keeps using the CPU.",
        )
        if dialog.exec():
            QMessageBox.information(self, "GPU acceleration ready", "Restart Clipivo to use your NVIDIA GPU.")
        self.refresh()


class SetupDialog(QDialog):
    """First launch: what was found and what is missing, with the download box."""

    def __init__(self, parent=None, engine=None, playback=None):
        super().__init__(parent)
        self.setWindowTitle("Finish setting up Clipivo")
        self.setModal(True)
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        intro = QLabel("Clipivo keeps its installer small and uses what is already on this computer. "
                       "This is what it found, and what it still needs:")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.panel = ComponentsPanel(engine, playback, self)
        layout.addWidget(self.panel)
        if any("Face models" in item.name for item in missing_downloads()):
            note = QLabel(downloads.FACE_MODEL_NOTICE)
            note.setWordWrap(True)
            note.setStyleSheet(f"color: {PALETTE['muted']}; font-size: 11px;")
            layout.addWidget(note)
        self.panel.box.finished.connect(lambda ok: self.accept() if ok and not missing_downloads() else None)
        later = QPushButton("Later")
        later.clicked.connect(self.reject)
        layout.addWidget(later, 0, Qt.AlignRight)

    def reject(self) -> None:
        self.panel.box.stop()
        super().reject()


def offer_downloads(parent, engine=None, playback=None) -> bool:
    """Ask for anything still missing. True when everything needed is now present."""
    if not missing_downloads():
        return True
    if QApplication.platformName() == "offscreen":
        # Nobody can answer a dialog here (tests, --self-test); report instead of waiting.
        return False
    SetupDialog(parent, engine, playback).exec()
    return not missing_downloads()


def open_vlc_page() -> None:
    QDesktopServices.openUrl(QUrl(VLC_URL))


def ffmpeg_missing_message() -> str:
    if sys.platform.startswith("linux"):
        return "Saving clips needs ffmpeg. Install it with your package manager, for example: sudo dnf install ffmpeg"
    return "Saving clips needs ffmpeg."
