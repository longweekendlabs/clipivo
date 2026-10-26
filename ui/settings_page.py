from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.face_engine import FaceEngine
from version import __app_name__, __org_name__, __tagline__, __version__

from .setup_dialog import ComponentsPanel
from .styles import PALETTE


class SettingsPage(QWidget):
    faceSettingsChanged = Signal(bool, int, float)
    exportSettingsChanged = Signal(str)
    defaultFolderChanged = Signal(str)
    aboutRequested = Signal()

    def __init__(self, face_engine: FaceEngine | None, parent=None, playback=None):
        super().__init__(parent)
        self.face_engine = face_engine
        self.settings = QSettings()
        # The page scrolls: a short window must not squash every row to a sliver.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        content.setObjectName("root")
        scroll.setWidget(content)
        outer.addWidget(scroll)
        root = QVBoxLayout(content)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        title = QLabel("Settings")
        title.setObjectName("pageTitle")
        title.setToolTip("Clipivo application and local recognition settings")
        root.addWidget(title)
        subtitle = QLabel("Face processing remains local. Changes apply to reference tests and later video analysis.")
        subtitle.setObjectName("pageSubtitle")
        subtitle.setToolTip("No character reference or video frame is sent to a cloud service")
        root.addWidget(subtitle)

        recognition = QGroupBox("Local face engine")
        recognition.setToolTip("Configure InsightFace detection and matching")
        form = QFormLayout(recognition)
        self.gpu = QCheckBox("Use the GPU when available")
        self.gpu.setChecked(self.settings.value("face/use_gpu", True, bool))
        self.gpu.setToolTip("CUDA on NVIDIA, DirectML on Windows, Core ML on macOS; otherwise the CPU")
        form.addRow("Device", self.gpu)

        self.detector = QComboBox()
        for label, value in (("Fast · 320 px", 320), ("Balanced · 640 px", 640), ("Small faces · 960 px", 960)):
            self.detector.addItem(label, value)
        detector_value = self.settings.value("face/detector_size", 640, int)
        self.detector.setCurrentIndex(max(0, self.detector.findData(detector_value)))
        self.detector.setToolTip("Larger detection input finds smaller faces but uses more processing time and memory")
        form.addRow("Detector size", self.detector)

        threshold_row = QWidget()
        threshold_layout = QHBoxLayout(threshold_row)
        threshold_layout.setContentsMargins(0, 0, 0, 0)
        self.threshold = QSlider(Qt.Horizontal)
        self.threshold.setRange(20, 80)
        self.threshold.setValue(round(self.settings.value("face/threshold", 0.40, float) * 100))
        self.threshold.setToolTip("Minimum cosine similarity required for a face match; higher is stricter")
        self.threshold_value = QLabel(f"{self.threshold.value() / 100:.2f}")
        self.threshold_value.setObjectName("settingsValue")
        self.threshold_value.setToolTip("Current face-match threshold")
        threshold_layout.addWidget(self.threshold, 1)
        threshold_layout.addWidget(self.threshold_value)
        form.addRow("Match strictness", threshold_row)

        root.addWidget(recognition)

        components = QGroupBox("What Clipivo uses")
        components.setToolTip("Face models, ffmpeg, VLC and the face-matching device: found or not, and where")
        components_layout = QVBoxLayout(components)
        self.components = ComponentsPanel(face_engine, playback, components)
        components_layout.addWidget(self.components)
        root.addWidget(components)

        export = QGroupBox("Export performance")
        export.setToolTip("Choose how Exact-cut MP4 video is encoded")
        export_form = QFormLayout(export)
        self.export_encoder = QComboBox()
        self.export_encoder.addItem("Auto · NVIDIA when available", "auto")
        self.export_encoder.addItem("NVIDIA NVENC", "nvenc")
        self.export_encoder.addItem("CPU · libx265", "cpu")
        export_value = self.settings.value("export/video_encoder", "auto", str)
        self.export_encoder.setCurrentIndex(max(0, self.export_encoder.findData(export_value)))
        self.export_encoder.setToolTip(
            "Auto uses NVIDIA NVENC when FFmpeg and the GPU support it, then falls back to CPU"
        )
        export_form.addRow("Exact-cut encoder", self.export_encoder)
        export_hint = QLabel(
            "NVENC accelerates HEVC (H.265) encoding. AV1 source decoding remains on CPU on GPUs without AV1 decode support."
        )
        export_hint.setWordWrap(True)
        export_hint.setStyleSheet(f"color: {PALETTE['muted']};")
        export_hint.setToolTip("The GTX 1660 Ti can encode HEVC but cannot hardware-decode AV1")
        export_form.addRow("", export_hint)
        root.addWidget(export)

        saving = QGroupBox("Saving")
        saving_form = QFormLayout(saving)
        folder_row = QHBoxLayout()
        self.default_folder_label = QLabel()
        self.default_folder_label.setObjectName("settingsValue")
        choose_folder = QPushButton("Choose…")
        choose_folder.clicked.connect(self._choose_default_folder)
        same_folder = QPushButton("Same folder as video")
        same_folder.clicked.connect(lambda: self._set_default_folder(""))
        folder_row.addWidget(self.default_folder_label, 1)
        folder_row.addWidget(choose_folder)
        folder_row.addWidget(same_folder)
        saving_form.addRow("Default save location", folder_row)
        saving_hint = QLabel("Video and Batch start here each time Clipivo opens. You can still pick another folder for one run.")
        saving_hint.setWordWrap(True)
        saving_hint.setStyleSheet(f"color: {PALETTE['muted']};")
        saving_form.addRow("", saving_hint)
        root.addWidget(saving)
        self._show_default_folder()

        shortcuts = QGroupBox("Keyboard shortcuts")
        shortcuts.setToolTip("Application-wide commands available from the keyboard")
        shortcuts_grid = QGridLayout(shortcuts)
        shortcuts_grid.setHorizontalSpacing(18)
        shortcut_rows = (("Save frame as PNG", "Ctrl+P", "Close video", "Ctrl+W"),)
        for row, values in enumerate(shortcut_rows):
            for column, value in enumerate(values):
                label = QLabel(value)
                label.setToolTip(f"Keyboard shortcut: {values[column + 1] if column % 2 == 0 else value}")
                if column % 2:
                    label.setObjectName("settingsValue")
                    label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                shortcuts_grid.addWidget(label, row, column)
            shortcuts_grid.setColumnStretch(0, 1)
            shortcuts_grid.setColumnStretch(2, 1)
        root.addWidget(shortcuts)

        privacy = QGroupBox("Privacy and model notice")
        privacy.setToolTip("How Clipivo stores references and the current model's distribution constraint")
        privacy_layout = QVBoxLayout(privacy)
        privacy_text = QLabel(
            "Saved people's photos are AES-256-GCM encrypted with a Clipivo-only key held by the OS keyring. "
            "One-time photos stay in memory and are gone when Clipivo closes.\n\n"
            "The face models are InsightFace's buffalo_l pack, published by InsightFace for "
            "non-commercial research use. Clipivo itself is MIT licensed; the models keep InsightFace's terms."
        )
        privacy_text.setWordWrap(True)
        privacy_text.setStyleSheet(f"color: {PALETTE['muted']};")
        privacy_text.setToolTip("Encrypted-storage and face-model licensing information")
        privacy_layout.addWidget(privacy_text)
        root.addWidget(privacy)

        about = QGroupBox(f"About {__app_name__}")
        about.setToolTip("Clipivo version and Long Weekend Labs branding")
        about_layout = QHBoxLayout(about)
        about_text = QLabel(
            f"<b>{__app_name__}</b> · Version {__version__}<br>"
            f"{__tagline__}<br><span style='color:{PALETTE['muted']}'>{__org_name__}</span>"
        )
        about_text.setTextFormat(Qt.RichText)
        about_text.setToolTip("Installed Clipivo version")
        about_button = QPushButton("About Clipivo")
        about_button.setToolTip("Open the full About and licensing window")
        about_button.clicked.connect(self.aboutRequested.emit)
        about_layout.addWidget(about_text)
        about_layout.addStretch()
        about_layout.addWidget(about_button)
        root.addWidget(about)
        root.addStretch()

        self.gpu.toggled.connect(self._changed)
        self.detector.currentIndexChanged.connect(self._changed)
        self.threshold.valueChanged.connect(self._threshold_changed)
        self.export_encoder.currentIndexChanged.connect(self._export_changed)
        self.refresh_engine_status()

    def default_folder(self) -> str:
        return str(self.settings.value("output/default_folder", "", str) or "")

    def _show_default_folder(self) -> None:
        folder = self.default_folder()
        self.default_folder_label.setText(folder or "Same folder as video")

    def _choose_default_folder(self) -> None:
        from .native_dialogs import choose_directory

        folder = choose_directory(self, "Default save location", key="default_output_folder")
        if folder:
            self._set_default_folder(folder)

    def _set_default_folder(self, folder: str) -> None:
        self.settings.setValue("output/default_folder", folder)
        self._show_default_folder()
        self.defaultFolderChanged.emit(folder)

    def values(self) -> tuple[bool, int, float]:
        return self.gpu.isChecked(), int(self.detector.currentData()), self.threshold.value() / 100.0

    def _threshold_changed(self, value: int) -> None:
        self.threshold_value.setText(f"{value / 100:.2f}")
        self._changed()

    def _changed(self) -> None:
        use_gpu, detector_size, threshold = self.values()
        self.settings.setValue("face/use_gpu", use_gpu)
        self.settings.setValue("face/detector_size", detector_size)
        self.settings.setValue("face/threshold", threshold)
        self.faceSettingsChanged.emit(use_gpu, detector_size, threshold)

    def export_encoder_value(self) -> str:
        return str(self.export_encoder.currentData())

    def _export_changed(self) -> None:
        value = self.export_encoder_value()
        self.settings.setValue("export/video_encoder", value)
        self.exportSettingsChanged.emit(value)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if hasattr(self, "components") and not self.components.box.busy():
            self.components.refresh()

    def refresh_engine_status(self) -> None:
        if hasattr(self, "components") and not self.components.box.busy():
            self.components.refresh()
