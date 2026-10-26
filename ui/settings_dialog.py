"""Clipivo's settings window.

A navigation rail beside a stack of short pages, the structure Parallax Studio and Topivo use:
each setting has one place and no page needs scrolling. The look is Clipivo's own palette and
type (ui/styles.py), not either of those apps'. Changes apply as they are made.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core import downloads
from core.face_engine import FaceEngine
from version import __app_name__, __org_name__, __tagline__, __version__

from .setup_dialog import ComponentsPanel, device_choice, device_status
from .styles import PALETTE

NAV_WIDTH = 176
P = PALETTE


def _caption(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setStyleSheet(f"font-size: 9px; font-weight: 700; color: {P['muted']}; letter-spacing: 1.4px;")
    return label


def _row(name: str, description: str, control: QWidget, control_width: int = 260) -> QWidget:
    """Name and explanation on the left, the control on the right."""
    row = QWidget()
    outer = QHBoxLayout(row)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(16)
    text = QVBoxLayout()
    text.setSpacing(2)
    title = QLabel(name)
    title.setStyleSheet(f"font-size: 12.5px; color: {P['text']}; font-weight: 600;")
    text.addWidget(title)
    if description:
        hint = QLabel(description)
        hint.setWordWrap(True)
        hint.setStyleSheet(f"font-size: 11px; color: {P['muted']};")
        text.addWidget(hint)
    outer.addLayout(text, 1)
    if control_width:
        control.setFixedWidth(control_width)
    outer.addWidget(control, 0, Qt.AlignVCenter)
    return row


def _rule() -> QFrame:
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {P['border']}; border: none;")
    return line


class _NavButton(QPushButton):
    def __init__(self, label: str):
        super().__init__(label)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(32)
        self.setStyleSheet(
            f"""
            QPushButton {{ background: transparent; border: none; border-radius: 6px; color: {P['secondary']};
                          font-size: 12.5px; padding: 0 10px; text-align: left; }}
            QPushButton:hover {{ background: {P['input']}; color: {P['text']}; }}
            QPushButton:checked {{ background: {P['input']}; color: {P['accent']}; font-weight: 600; }}
            """
        )


DEVICE_LABELS = {
    "linux": ("GPU (NVIDIA CUDA)", "Automatic uses your NVIDIA card through CUDA when it is set up."),
    "win32": ("GPU (DirectML)", "Automatic uses your graphics card through DirectML: NVIDIA, AMD or Intel."),
    "darwin": ("GPU (Core ML)", "Automatic uses the Apple Silicon GPU through Core ML."),
}


class SettingsDialog(QDialog):
    faceSettingsChanged = Signal(bool, int, float)
    exportSettingsChanged = Signal(str)
    defaultFolderChanged = Signal(str)
    aboutRequested = Signal()
    deviceChanged = Signal()

    def __init__(self, face_engine: FaceEngine | None, parent=None, playback=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setFixedSize(860, 580)
        self.face_engine = face_engine
        self.settings = QSettings()
        self._pages: dict[str, int] = {}
        self._nav: dict[str, _NavButton] = {}
        self.setStyleSheet(
            f"""
            QDialog {{ background: {P['base']}; }}
            QLabel {{ background: transparent; }}
            QPushButton#footer {{ background: {P['input']}; border: 1px solid {P['border']}; border-radius: 4px;
                                  color: {P['text']}; padding: 6px 18px; }}
            QPushButton#footer:hover {{ border-color: {P['accent']}; color: {P['accent']}; }}
            """
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_nav())
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)

        self._add("face", self._face_page())
        self.components = ComponentsPanel(face_engine, playback)
        self._add("components", self._page("What Clipivo uses", self.components,
                                           "Everything Clipivo relies on: found or not, and where."))
        self._add("saving", self._saving_page())
        self._add("shortcuts", self._shortcuts_page())
        self._add("about", self._about_page())

        footer = QWidget()
        footer.setStyleSheet(f"border-top: 1px solid {P['border']};")
        row = QHBoxLayout(footer)
        row.setContentsMargins(22, 10, 22, 12)
        note = QLabel("Changes apply as you make them.")
        note.setStyleSheet(f"color: {P['muted']}; font-size: 11px; border: none;")
        row.addWidget(note)
        row.addStretch()
        close = QPushButton("Close")
        close.setObjectName("footer")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        outer.addWidget(footer)
        self.open_page("face")

    # Structure --------------------------------------------------------------------------

    def _build_nav(self) -> QWidget:
        rail = QWidget()
        rail.setObjectName("settingsRail")
        rail.setFixedWidth(NAV_WIDTH)
        rail.setStyleSheet(f"QWidget#settingsRail {{ background: {P['panel']}; border-right: 1px solid {P['border']}; }}")
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(10, 16, 10, 12)
        layout.setSpacing(2)
        for caption, entries in (
            ("Clipivo", (("face", "Face matching"), ("saving", "Saving"), ("shortcuts", "Shortcuts"))),
            ("System", (("components", "What Clipivo uses"),)),
            ("Help", (("about", "About and privacy"),)),
        ):
            layout.addWidget(_caption(caption))
            layout.addSpacing(2)
            for page_id, label in entries:
                button = _NavButton(label)
                button.clicked.connect(lambda _checked=False, page=page_id: self.open_page(page))
                self._nav[page_id] = button
                layout.addWidget(button)
            layout.addSpacing(14)
        layout.addStretch()
        return rail

    def _page(self, title: str, content: QWidget | None = None, subtitle: str = "") -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(26, 20, 26, 16)
        layout.setSpacing(14)
        heading = QLabel(title)
        heading.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {P['text']};")
        layout.addWidget(heading)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setWordWrap(True)
            sub.setStyleSheet(f"color: {P['muted']}; font-size: 11.5px;")
            layout.addWidget(sub)
        if content is not None:
            layout.addWidget(content)
            layout.addStretch()
        page.body = layout  # type: ignore[attr-defined]
        return page

    def _add(self, page_id: str, page: QWidget) -> None:
        self._pages[page_id] = self.stack.addWidget(page)

    def open_page(self, page_id: str) -> None:
        if page_id not in self._pages:
            return
        self.stack.setCurrentIndex(self._pages[page_id])
        for key, button in self._nav.items():
            button.setChecked(key == page_id)
        if page_id in ("face", "components"):
            self.refresh_engine_status()

    # Pages ------------------------------------------------------------------------------

    def _face_page(self) -> QWidget:
        page = self._page("Face matching", subtitle="Finding and recognising faces runs on this computer only.")
        layout = page.body
        self.device = QComboBox()
        gpu_label, gpu_hint = DEVICE_LABELS.get(sys.platform, DEVICE_LABELS["linux"])
        self.device.addItem("Automatic", "auto")
        self.device.addItem(gpu_label, "gpu")
        self.device.addItem("CPU", "cpu")
        self.device.setCurrentIndex(max(0, self.device.findData(device_choice())))
        layout.addWidget(_row(
            "Device",
            f"{gpu_hint} The graphics card is many times faster than the CPU; results are the same.",
            self.device,
        ))
        # Where it runs right now, and what to do about it.
        self.device_status = QLabel()
        self.device_status.setWordWrap(True)
        self.device_status.setTextFormat(Qt.RichText)
        self.device_action = QPushButton()
        self.device_action.clicked.connect(lambda: self.open_page("components"))
        status_row = QHBoxLayout()
        status_row.addWidget(self.device_status, 1)
        status_row.addWidget(self.device_action, 0, Qt.AlignVCenter)
        layout.addLayout(status_row)
        layout.addWidget(_rule())

        self.detector = QComboBox()
        for label, value in (("Fast · 320 px", 320), ("Balanced · 640 px", 640), ("Small faces · 960 px", 960)):
            self.detector.addItem(label, value)
        self.detector.setCurrentIndex(max(0, self.detector.findData(self.settings.value("face/detector_size", 640, int))))
        layout.addWidget(_row("Detector size", "Larger finds smaller faces, and takes longer.", self.detector))

        strictness = QWidget()
        strictness_row = QHBoxLayout(strictness)
        strictness_row.setContentsMargins(0, 0, 0, 0)
        self.threshold = QSlider(Qt.Horizontal)
        self.threshold.setRange(20, 80)
        self.threshold.setValue(round(self.settings.value("face/threshold", 0.40, float) * 100))
        self.threshold_value = QLabel(f"{self.threshold.value() / 100:.2f}")
        self.threshold_value.setObjectName("settingsValue")
        strictness_row.addWidget(self.threshold, 1)
        strictness_row.addWidget(self.threshold_value)
        layout.addWidget(_row("Match strictness", "Higher misses fewer wrong people and more of the right one.", strictness))
        layout.addStretch()

        self.device.currentIndexChanged.connect(self._changed)
        self.detector.currentIndexChanged.connect(self._changed)
        self.threshold.valueChanged.connect(self._threshold_changed)
        return page

    def _saving_page(self) -> QWidget:
        page = self._page("Saving")
        layout = page.body
        folder = QWidget()
        folder_row = QVBoxLayout(folder)
        folder_row.setContentsMargins(0, 0, 0, 0)
        self.default_folder_label = QLabel()
        self.default_folder_label.setObjectName("settingsValue")
        self.default_folder_label.setWordWrap(True)
        buttons = QHBoxLayout()
        choose = QPushButton("Choose…")
        choose.clicked.connect(self._choose_default_folder)
        same = QPushButton("Same folder as video")
        same.clicked.connect(lambda: self._set_default_folder(""))
        buttons.addWidget(choose)
        buttons.addWidget(same)
        folder_row.addWidget(self.default_folder_label)
        folder_row.addLayout(buttons)
        layout.addWidget(_row("Default save location",
                              "Video and Batch start here each time Clipivo opens. You can still pick another folder for one run.",
                              folder, 300))
        layout.addWidget(_rule())
        self.export_encoder = QComboBox()
        self.export_encoder.addItem("Auto · graphics card when available", "auto")
        self.export_encoder.addItem("NVIDIA NVENC", "nvenc")
        self.export_encoder.addItem("CPU · libx265", "cpu")
        self.export_encoder.setCurrentIndex(max(0, self.export_encoder.findData(self.settings.value("export/video_encoder", "auto", str))))
        self.export_encoder.setToolTip("Auto uses NVIDIA NVENC when ffmpeg and the card support it, otherwise the CPU")
        layout.addWidget(_row("Video encoder",
                              "Re-encoded clips are HEVC (H.265). NVENC encodes on an NVIDIA card; otherwise the CPU does.",
                              self.export_encoder))
        layout.addStretch()
        self._show_default_folder()
        self.export_encoder.currentIndexChanged.connect(self._export_changed)
        return page

    def _shortcuts_page(self) -> QWidget:
        page = self._page("Shortcuts")
        layout = page.body
        for name, keys in (
            ("Open video", "Ctrl+O"), ("Save frame as PNG", "Ctrl+P"), ("Close video", "Ctrl+W"),
            ("Set A / set B", "A / B"), ("Add the range from A to B as a clip", "Ctrl+Enter"),
        ):
            key = QLabel(keys)
            key.setObjectName("settingsValue")
            key.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            layout.addWidget(_row(name, "", key, 160))
        layout.addStretch()
        return page

    def _about_page(self) -> QWidget:
        page = self._page("About and privacy")
        layout = page.body
        about = QLabel(f"<b>{__app_name__}</b> · Version {__version__}<br>{__tagline__}<br>"
                       f"<span style='color:{P['muted']}'>{__org_name__} · MIT licence</span>")
        about.setTextFormat(Qt.RichText)
        button = QPushButton("About Clipivo")
        button.clicked.connect(self.aboutRequested.emit)
        layout.addWidget(_row("Clipivo", "", about, 0))
        layout.addWidget(button, 0, Qt.AlignLeft)
        layout.addWidget(_rule())
        privacy = QLabel(
            "Saved people's photos are encrypted (AES-256-GCM) with a key kept in your system's keyring. "
            "One-time photos stay in memory and are gone when Clipivo closes. Nothing is sent anywhere.\n\n"
            "The face models are InsightFace's buffalo_l pack, published by InsightFace for non-commercial "
            "research use; they keep InsightFace's terms."
        )
        privacy.setWordWrap(True)
        privacy.setStyleSheet(f"color: {P['secondary']};")
        layout.addWidget(privacy)
        layout.addStretch()
        return page

    # Values -----------------------------------------------------------------------------

    def device_value(self) -> str:
        return str(self.device.currentData())

    def values(self) -> tuple[bool, int, float]:
        return self.device_value() != "cpu", int(self.detector.currentData()), self.threshold.value() / 100.0

    def _threshold_changed(self, value: int) -> None:
        self.threshold_value.setText(f"{value / 100:.2f}")
        self._changed()

    def _changed(self) -> None:
        use_gpu, detector_size, threshold = self.values()
        self.settings.setValue("face/device", self.device_value())
        self.settings.setValue("face/use_gpu", use_gpu)
        self.settings.setValue("face/detector_size", detector_size)
        self.settings.setValue("face/threshold", threshold)
        self.faceSettingsChanged.emit(use_gpu, detector_size, threshold)
        self.deviceChanged.emit()
        self.refresh_engine_status()

    def refresh_engine_status(self) -> None:
        mark, detail = device_status(self.face_engine)
        colour = P["ready"] if mark is True and detail.startswith("On the GPU") else (P["accent"] if mark is not True else P["secondary"])
        self.device_status.setText(f"<span style='color:{colour}; font-weight:600'>Now:</span> "
                                   f"<span style='color:{P['secondary']}'>{detail}</span>")
        needs_download = self.device_value() != "cpu" and downloads.gpu_pack_offered() and not downloads.gpu_pack_installed()
        self.device_action.setVisible(needs_download)
        self.device_action.setText("Set up the GPU…")
        if not self.components.box.busy():
            self.components.refresh()

    def default_folder(self) -> str:
        return str(self.settings.value("output/default_folder", "", str) or "")

    def _show_default_folder(self) -> None:
        self.default_folder_label.setText(self.default_folder() or "Same folder as video")

    def _choose_default_folder(self) -> None:
        from .native_dialogs import choose_directory

        folder = choose_directory(self, "Default save location", key="default_output_folder")
        if folder:
            self._set_default_folder(folder)

    def _set_default_folder(self, folder: str) -> None:
        self.settings.setValue("output/default_folder", folder)
        self._show_default_folder()
        self.defaultFolderChanged.emit(folder)

    def export_encoder_value(self) -> str:
        return str(self.export_encoder.currentData())

    def _export_changed(self) -> None:
        value = self.export_encoder_value()
        self.settings.setValue("export/video_encoder", value)
        self.exportSettingsChanged.emit(value)
