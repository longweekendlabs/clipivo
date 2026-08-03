from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QProcess, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDial,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.filters import (
    FILTER_CATALOG,
    VideoFilter,
    create_filter,
    describe_filter,
    filtered_dimensions,
    video_filtergraph,
)
from core.models import VideoInfo
from core.timecode import format_timecode

from .icons import line_icon
from .styles import PALETTE
from .timeline import DetectionTimeline


class FilterPreviewCanvas(QWidget):
    cropChanged = Signal(int, int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.input_pixmap = QPixmap()
        self.output_pixmap = QPixmap()
        self.mode = "output"
        self.crop_editing = False
        self.crop_source_size = (2, 2)
        self.crop_margins = (0, 0, 0, 0)
        self._drag_part: str | None = None
        self._drag_start = QPoint()
        self._drag_origin = (0, 0, 0, 0)
        self.setMinimumSize(640, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)

    def set_input(self, pixmap: QPixmap) -> None:
        self.input_pixmap = pixmap.copy()
        self.update()

    def set_output(self, pixmap: QPixmap) -> None:
        self.output_pixmap = pixmap.copy()
        self.update()

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.update()

    def set_crop_editor(
        self,
        enabled: bool,
        source_width: int = 2,
        source_height: int = 2,
        margins: tuple[int, int, int, int] = (0, 0, 0, 0),
    ) -> None:
        self.crop_editing = enabled
        self.crop_source_size = (max(2, source_width), max(2, source_height))
        self.crop_margins = tuple(max(0, int(value)) // 2 * 2 for value in margins)
        self._drag_part = None
        self.update()

    def set_crop_margins(self, left: int, right: int, top: int, bottom: int) -> None:
        self.crop_margins = tuple(max(0, int(value)) // 2 * 2 for value in (left, right, top, bottom))
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#080a0d"))
        if self.input_pixmap.isNull():
            painter.setPen(QColor(PALETTE["muted"]))
            painter.drawText(self.rect(), Qt.AlignCenter, "Open a video to preview filters")
            return
        output = self.output_pixmap if not self.output_pixmap.isNull() else self.input_pixmap
        if self.mode == "side":
            left = self.rect().adjusted(0, 0, -self.width() // 2, 0)
            right = self.rect().adjusted(self.width() // 2, 0, 0, 0)
            self._paint_pixmap(painter, left, self.input_pixmap, "Input")
            self._paint_pixmap(painter, right, output, "Output")
            painter.setPen(QColor(PALETTE["border"]))
            painter.drawLine(self.width() // 2, 0, self.width() // 2, self.height())
        elif self.mode == "input":
            self._paint_pixmap(painter, self.rect(), self.input_pixmap, "Input")
        else:
            label = "Crop editor" if self.mode == "crop" else "Filtered output"
            image_rect = self._paint_pixmap(painter, self.rect(), output, label)
            if self.mode == "crop" and self.crop_editing:
                self._paint_crop_overlay(painter, image_rect)

    @staticmethod
    def _paint_pixmap(painter: QPainter, rect: QRect, pixmap: QPixmap, label: str) -> QRect:
        target = rect.adjusted(5, 5, -5, -5)
        scaled = pixmap.scaled(target.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        x = target.x() + (target.width() - scaled.width()) // 2
        y = target.y() + (target.height() - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
        painter.fillRect(QRect(rect.x() + 9, rect.y() + 9, 100, 22), QColor(0, 0, 0, 155))
        painter.setPen(QColor(PALETTE["secondary"]))
        font = QFont("Inter")
        font.setPixelSize(11)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRect(rect.x() + 15, rect.y() + 9, 90, 22), Qt.AlignVCenter, label)
        return QRect(x, y, scaled.width(), scaled.height())

    def _display_rect(self) -> QRect:
        pixmap = self.output_pixmap if not self.output_pixmap.isNull() else self.input_pixmap
        if pixmap.isNull():
            return QRect()
        target = self.rect().adjusted(5, 5, -5, -5)
        scaled = pixmap.size().scaled(target.size(), Qt.KeepAspectRatio)
        return QRect(
            target.x() + (target.width() - scaled.width()) // 2,
            target.y() + (target.height() - scaled.height()) // 2,
            scaled.width(),
            scaled.height(),
        )

    def _crop_rect(self, image_rect: QRect | None = None) -> QRect:
        image_rect = image_rect or self._display_rect()
        width, height = self.crop_source_size
        left, right, top, bottom = self.crop_margins
        x1 = image_rect.left() + round(left * image_rect.width() / width)
        x2 = image_rect.right() - round(right * image_rect.width() / width)
        y1 = image_rect.top() + round(top * image_rect.height() / height)
        y2 = image_rect.bottom() - round(bottom * image_rect.height() / height)
        return QRect(QPoint(x1, y1), QPoint(max(x1 + 1, x2), max(y1 + 1, y2)))

    def _paint_crop_overlay(self, painter: QPainter, image_rect: QRect) -> None:
        crop = self._crop_rect(image_rect)
        shade = QColor(0, 0, 0, 170)
        painter.fillRect(QRect(image_rect.left(), image_rect.top(), image_rect.width(), max(0, crop.top() - image_rect.top())), shade)
        painter.fillRect(QRect(image_rect.left(), crop.bottom() + 1, image_rect.width(), max(0, image_rect.bottom() - crop.bottom())), shade)
        painter.fillRect(QRect(image_rect.left(), crop.top(), max(0, crop.left() - image_rect.left()), crop.height()), shade)
        painter.fillRect(QRect(crop.right() + 1, crop.top(), max(0, image_rect.right() - crop.right()), crop.height()), shade)

        painter.setPen(QPen(QColor(PALETTE["accent"]), 2))
        painter.drawRect(crop)
        painter.setPen(QPen(QColor(255, 255, 255, 115), 1, Qt.DashLine))
        painter.drawLine(crop.left() + crop.width() // 3, crop.top(), crop.left() + crop.width() // 3, crop.bottom())
        painter.drawLine(crop.left() + 2 * crop.width() // 3, crop.top(), crop.left() + 2 * crop.width() // 3, crop.bottom())
        painter.drawLine(crop.left(), crop.top() + crop.height() // 3, crop.right(), crop.top() + crop.height() // 3)
        painter.drawLine(crop.left(), crop.top() + 2 * crop.height() // 3, crop.right(), crop.top() + 2 * crop.height() // 3)

        painter.setPen(QPen(QColor(PALETTE["accent_text"]), 1))
        painter.setBrush(QColor(PALETTE["accent"]))
        for point in self._handle_points(crop).values():
            painter.drawRect(QRect(point.x() - 5, point.y() - 5, 10, 10))

        output_width = max(2, self.crop_source_size[0] - self.crop_margins[0] - self.crop_margins[1])
        output_height = max(2, self.crop_source_size[1] - self.crop_margins[2] - self.crop_margins[3])
        text = f"{output_width} × {output_height}"
        badge = QRect(crop.left() + 8, crop.top() + 8, 108, 24)
        painter.fillRect(badge, QColor(0, 0, 0, 185))
        painter.setPen(QColor(PALETTE["text"]))
        painter.drawText(badge.adjusted(7, 0, -4, 0), Qt.AlignVCenter, text)

    @staticmethod
    def _handle_points(crop: QRect) -> dict[str, QPoint]:
        center_x, center_y = crop.center().x(), crop.center().y()
        return {
            "top_left": crop.topLeft(), "top": QPoint(center_x, crop.top()), "top_right": crop.topRight(),
            "left": QPoint(crop.left(), center_y), "right": QPoint(crop.right(), center_y),
            "bottom_left": crop.bottomLeft(), "bottom": QPoint(center_x, crop.bottom()), "bottom_right": crop.bottomRight(),
        }

    def _hit_part(self, position: QPoint) -> str | None:
        if not self.crop_editing or self.mode != "crop":
            return None
        crop = self._crop_rect()
        for name, point in self._handle_points(crop).items():
            if QRect(point.x() - 9, point.y() - 9, 18, 18).contains(position):
                return name
        if crop.adjusted(9, 9, -9, -9).contains(position):
            return "move"
        return None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            part = self._hit_part(event.position().toPoint())
            if part:
                self._drag_part = part
                self._drag_start = event.position().toPoint()
                self._drag_origin = self.crop_margins
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        position = event.position().toPoint()
        if not self._drag_part:
            part = self._hit_part(position)
            if part == "move":
                self.setCursor(Qt.SizeAllCursor)
            elif part in ("left", "right"):
                self.setCursor(Qt.SizeHorCursor)
            elif part in ("top", "bottom"):
                self.setCursor(Qt.SizeVerCursor)
            elif part in ("top_left", "bottom_right"):
                self.setCursor(Qt.SizeFDiagCursor)
            elif part in ("top_right", "bottom_left"):
                self.setCursor(Qt.SizeBDiagCursor)
            else:
                self.unsetCursor()
            return

        image_rect = self._display_rect()
        if image_rect.width() < 1 or image_rect.height() < 1:
            return
        width, height = self.crop_source_size
        dx = round((position.x() - self._drag_start.x()) * width / image_rect.width())
        dy = round((position.y() - self._drag_start.y()) * height / image_rect.height())
        left, right, top, bottom = self._drag_origin
        minimum = 2
        part = self._drag_part
        if part == "move":
            dx = max(-left, min(right, dx))
            dy = max(-top, min(bottom, dy))
            left, right, top, bottom = left + dx, right - dx, top + dy, bottom - dy
        else:
            if "left" in part:
                left = max(0, min(width - right - minimum, left + dx))
            if "right" in part:
                right = max(0, min(width - left - minimum, right - dx))
            if "top" in part:
                top = max(0, min(height - bottom - minimum, top + dy))
            if "bottom" in part:
                bottom = max(0, min(height - top - minimum, bottom - dy))
        values = tuple(max(0, value) // 2 * 2 for value in (left, right, top, bottom))
        if values != self.crop_margins:
            self.crop_margins = values
            self.cropChanged.emit(*values)
            self.update()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._drag_part:
            self._drag_part = None
            event.accept()
            return
        super().mouseReleaseEvent(event)


class HistogramWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.values = [0] * 256
        self.setMinimumSize(256, 105)
        self.setMaximumHeight(125)

    def set_pixmap(self, pixmap: QPixmap) -> None:
        values = [0] * 256
        if not pixmap.isNull():
            image = pixmap.toImage().scaled(256, 256, Qt.KeepAspectRatio, Qt.FastTransformation)
            for y in range(image.height()):
                for x in range(image.width()):
                    color = image.pixelColor(x, y)
                    luma = min(255, max(0, round(0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue())))
                    values[luma] += 1
        self.values = values
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0d1116"))
        peak = max(1, max(self.values))
        painter.setPen(QPen(QColor(PALETTE["secondary"]), 1))
        for x, value in enumerate(self.values):
            px = round(x * max(1, self.width() - 1) / 255)
            height = round((value / peak) ** 0.45 * max(1, self.height() - 8))
            painter.drawLine(px, self.height() - 2, px, self.height() - 2 - height)
        painter.setPen(QPen(QColor(PALETTE["accent"]), 1))
        painter.drawLine(16 * self.width() // 256, 0, 16 * self.width() // 256, self.height())
        painter.drawLine(235 * self.width() // 256, 0, 235 * self.width() // 256, self.height())


class FilterConfigDialog(QDialog):
    def __init__(
        self,
        info: VideoInfo,
        source_pixmap: QPixmap,
        current_ms: int,
        prefix_filters: list[VideoFilter],
        candidate: VideoFilter | None,
        parent=None,
        marker_a: int | None = None,
        marker_b: int | None = None,
    ):
        super().__init__(parent)
        self.video_info = info
        self.source_pixmap = source_pixmap.copy()
        self.current_ms = current_ms
        self.marker_a = marker_a
        self.marker_b = marker_b
        self.prefix_filters = [VideoFilter.from_dict(item.to_dict()) for item in prefix_filters]
        self.candidate = VideoFilter.from_dict(candidate.to_dict()) if candidate else None
        self._preview_process: QProcess | None = None
        self._original_preview_process: QProcess | None = None
        self._play_process: QProcess | None = None
        self._original_play_process: QProcess | None = None
        self._play_buffer = bytearray()
        self._original_play_buffer = bytearray()
        self._play_start_ms = 0
        self._play_end_ms = 0
        self._play_frame_index = 0
        self._updating_seek = False
        self._crop_spins: dict[str, QSpinBox] = {}
        self._controls: dict[str, QWidget] = {}
        self._value_labels: dict[str, QLabel] = {}
        self.histogram: HistogramWidget | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._render_preview)

        title = self.candidate.name if self.candidate else "Preview active filters"
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(1120, 820)
        self.setMinimumSize(820, 620)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 7, 8, 7)
        root.setSpacing(5)

        if self.candidate:
            settings = self._build_settings()
            if settings is not None:
                root.addWidget(settings)

        navigation = QHBoxLayout()
        self.previous_button = QPushButton("◀ Frame")
        self.next_button = QPushButton("Frame ▶")
        self.previous_button.setToolTip("Preview the previous source frame")
        self.next_button.setToolTip("Preview the next source frame")
        self.previous_button.clicked.connect(lambda: self._step_frame(-1))
        self.next_button.clicked.connect(lambda: self._step_frame(1))
        navigation.addWidget(self.previous_button)
        navigation.addWidget(self.next_button)
        self.play_button = QPushButton("▶ Play")
        self.play_button.setToolTip("Play or pause the filter preview")
        self.play_button.clicked.connect(lambda: self._toggle_playback(False))
        navigation.addWidget(self.play_button)
        self.play_ab_button = QPushButton("▶ Play A–B")
        self.play_ab_button.setEnabled(
            marker_a is not None and marker_b is not None and marker_b > marker_a
        )
        if self.play_ab_button.isEnabled():
            self.play_ab_button.setToolTip(
                f"Play {format_timecode(marker_a)} to {format_timecode(marker_b)}"
            )
        else:
            self.play_ab_button.setToolTip("Set a valid A and B range in the Video tab first")
        self.play_ab_button.clicked.connect(lambda: self._toggle_playback(True))
        navigation.addWidget(self.play_ab_button)
        self.time_label = QLabel(format_timecode(current_ms))
        self.time_label.setObjectName("timecode")
        navigation.addWidget(self.time_label)
        navigation.addStretch()
        self.peek_button = QPushButton("Show original")
        self.peek_button.setToolTip("Hold the current position while toggling between original and filtered output")
        self.peek_button.setCheckable(True)
        self.peek_button.toggled.connect(self._toggle_original)
        if self.candidate and self.candidate.kind == "crop":
            self.peek_button.hide()
        navigation.addWidget(self.peek_button)
        root.addLayout(navigation)

        self.preview_canvas = FilterPreviewCanvas()
        self.preview_canvas.setToolTip("Filter preview; crop filters expose draggable coral handles")
        self.preview_canvas.set_input(self.source_pixmap)
        self.preview_canvas.cropChanged.connect(self._crop_overlay_changed)
        root.addWidget(self.preview_canvas, 1)

        self.seek = DetectionTimeline()
        self.seek.setToolTip("Seek the filter preview with the same source-video timebase")
        self.seek.setRange(0, max(1, info.duration_ms))
        self.seek.marker_a = marker_a
        self.seek.marker_b = marker_b
        self.seek.setValue(current_ms)
        self.seek.valueChanged.connect(self._seek_changed)
        root.addWidget(self.seek)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Reset)
        buttons.setToolTip("Apply, discard, or reset this filter configuration")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Reset).clicked.connect(self._reset)
        root.addWidget(buttons)

        self._configure_canvas()
        self._schedule_preview()

    def result_filter(self) -> VideoFilter | None:
        return VideoFilter.from_dict(self.candidate.to_dict()) if self.candidate else None

    def _build_settings(self) -> QWidget | None:
        kind = self.candidate.kind
        if kind == "crop":
            return self._build_crop_settings()
        if kind == "contrast":
            return self._build_contrast_settings()
        if kind == "color_balance":
            return self._build_color_balance_settings()

        box = QGroupBox(f"{self.candidate.name} settings")
        form = QFormLayout(box)
        if kind == "rotate":
            angle = QComboBox()
            angle.setToolTip("Clockwise rotation applied to the video")
            for value in (90, 180, 270):
                angle.addItem(f"{value}°", value)
            angle.setCurrentIndex(max(0, angle.findData(int(self.candidate.params.get("angle", 90)))))
            angle.currentIndexChanged.connect(lambda: self._set_param("angle", int(angle.currentData())))
            self._controls["angle"] = angle
            form.addRow("Angle", angle)
        elif kind == "vertical_flip":
            form.addRow(QLabel("The frame will be flipped vertically."))
        elif kind == "frame_rate":
            mode = QComboBox()
            mode.setToolTip("Resample keeps duration; change speed creates slow or fast motion")
            mode.addItem("Resample — keep duration", "resample")
            mode.addItem("Change speed — slow/fast motion", "speed")
            mode.setCurrentIndex(max(0, mode.findData(self.candidate.params.get("mode", "resample"))))
            fps = QDoubleSpinBox()
            fps.setToolTip("Output frame rate")
            fps.setRange(1.0, 240.0)
            fps.setDecimals(3)
            fps.setSuffix(" fps")
            fps.setValue(float(self.candidate.params.get("fps", self.video_info.fps)))
            blend = QCheckBox("Blend interpolated frames")
            blend.setToolTip("Blend neighboring frames when resampling")
            blend.setChecked(bool(self.candidate.params.get("blend", False)))
            mode.currentIndexChanged.connect(lambda: self._set_param("mode", str(mode.currentData())))
            fps.valueChanged.connect(lambda value: self._set_param("fps", float(value)))
            blend.toggled.connect(lambda value: self._set_param("blend", value))
            self._controls.update({"mode": mode, "fps": fps, "blend": blend})
            form.addRow("Behavior", mode)
            form.addRow("Output rate", fps)
            form.addRow("Interpolation", blend)
        return box

    def _build_crop_settings(self) -> QWidget:
        box = QGroupBox("Crop")
        layout = QGridLayout(box)
        width, height = filtered_dimensions(self.prefix_filters, self.video_info.width, self.video_info.height)
        for column, (label, key, maximum) in enumerate((
            ("Left", "left", width), ("Right", "right", width),
            ("Top", "top", height), ("Bottom", "bottom", height),
        )):
            spin = QSpinBox()
            spin.setToolTip(f"Pixels removed from the {label.lower()} edge; crop values remain even")
            spin.setRange(0, max(0, maximum - 2))
            spin.setSingleStep(2)
            spin.setValue(int(self.candidate.params.get(key, 0)))
            spin.valueChanged.connect(lambda value, k=key: self._crop_spin_changed(k, value))
            layout.addWidget(QLabel(label), 0, column * 2)
            layout.addWidget(spin, 0, column * 2 + 1)
            self._crop_spins[key] = spin
            self._controls[key] = spin
        self.crop_size = QLabel()
        self.crop_size.setObjectName("cropOutput")
        layout.addWidget(QLabel("Output"), 1, 0)
        layout.addWidget(self.crop_size, 1, 1, 1, 2)
        self._update_crop_size()
        return box

    def _build_contrast_settings(self) -> QWidget:
        box = QGroupBox("Contrast")
        layout = QHBoxLayout(box)
        contrast_widget, contrast, contrast_label = self._slider_with_label("Contrast", 50, 150, round(float(self.candidate.params.get("contrast", 1.0)) * 100), "%")
        brightness_widget, brightness, brightness_label = self._slider_with_label("Brightness", -127, 127, round(float(self.candidate.params.get("brightness", 0.0)) * 127), "")
        contrast.valueChanged.connect(lambda value: (contrast_label.setText(f"Contrast: {value}%"), self._set_param("contrast", value / 100.0)))
        brightness.valueChanged.connect(lambda value: (brightness_label.setText(f"Brightness: {value}"), self._set_param("brightness", value / 127.0)))
        self._controls.update({"contrast": contrast, "brightness": brightness})
        self._value_labels.update({"contrast": contrast_label, "brightness": brightness_label})
        layout.addWidget(contrast_widget)
        layout.addWidget(brightness_widget)

        planes = QVBoxLayout()
        mpeg = QPushButton("MPEG2 → PC")
        mpeg.setToolTip("Apply Avidemux-style TV-range to full-range contrast values")
        mpeg.clicked.connect(lambda: self._mpeg_to_pc(contrast, brightness))
        planes.addWidget(mpeg)
        for text, key in (("Luma", "luma"), ("Chroma U", "chroma_u"), ("Chroma V", "chroma_v")):
            check = QCheckBox(text)
            check.setToolTip(f"Apply contrast adjustment to {text.lower()}")
            check.setChecked(bool(self.candidate.params.get(key, True)))
            check.toggled.connect(lambda value, k=key: self._set_param(k, value))
            self._controls[key] = check
            planes.addWidget(check)
        planes.addStretch()
        layout.addLayout(planes)
        self.histogram = HistogramWidget()
        layout.addWidget(self.histogram, 1)
        return box

    def _build_color_balance_settings(self) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        for title, prefix in (("Shadow", "shadow"), ("Midtone", "midtone"), ("Highlight", "highlight")):
            group = QGroupBox(title)
            grid = QGridLayout(group)
            hue = QDial()
            hue.setRange(0, 359)
            hue.setWrapping(True)
            hue.setNotchesVisible(True)
            hue.setValue(round(float(self.candidate.params.get(f"{prefix}_angle", 0.0))))
            hue.valueChanged.connect(lambda value, p=prefix: self._set_param(f"{p}_angle", float(value)))
            self._controls[f"{prefix}_angle"] = hue
            grid.addWidget(QLabel("Hue"), 0, 0)
            grid.addWidget(hue, 0, 1, 1, 2)
            for row, (label, suffix, minimum, maximum) in enumerate((
                ("Luma", "luma", -100, 100),
                ("Chroma shift", "shift", 0, 100),
                ("Saturation", "saturation", -100, 100),
            ), start=1):
                slider = QSlider(Qt.Horizontal)
                slider.setToolTip(f"{title} {label.lower()} adjustment")
                slider.setRange(minimum, maximum)
                slider.setValue(round(float(self.candidate.params.get(f"{prefix}_{suffix}", 0.0)) * 100))
                value_label = QLabel(str(slider.value()))
                slider.valueChanged.connect(lambda value, p=prefix, s=suffix, out=value_label: (out.setText(str(value)), self._set_param(f"{p}_{s}", value / 100.0)))
                self._controls[f"{prefix}_{suffix}"] = slider
                self._value_labels[f"{prefix}_{suffix}"] = value_label
                grid.addWidget(QLabel(label), row, 0)
                grid.addWidget(slider, row, 1)
                grid.addWidget(value_label, row, 2)
            layout.addWidget(group, 1)
        return container

    def _slider_with_label(self, title: str, minimum: int, maximum: int, value: int, suffix: str) -> tuple[QWidget, QSlider, QLabel]:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(2, 2, 2, 2)
        label = QLabel(f"{title}: {value}{suffix}")
        slider = QSlider(Qt.Horizontal)
        slider.setToolTip(f"Adjust {title.lower()} from {minimum} to {maximum}")
        slider.setRange(minimum, maximum)
        slider.setValue(value)
        layout.addWidget(label)
        layout.addWidget(slider)
        return widget, slider, label

    def _mpeg_to_pc(self, contrast: QSlider, brightness: QSlider) -> None:
        contrast.setValue(116)
        brightness.setValue(-16)

    def _set_param(self, key: str, value) -> None:
        self.candidate.params[key] = value
        self._stop_playback(schedule_preview=False)
        self._schedule_preview()

    def _configure_canvas(self) -> None:
        if self.candidate and self.candidate.kind == "crop":
            width, height = filtered_dimensions(self.prefix_filters, self.video_info.width, self.video_info.height)
            params = self.candidate.params
            self.preview_canvas.set_mode("crop")
            self.preview_canvas.set_crop_editor(True, width, height, (
                int(params.get("left", 0)), int(params.get("right", 0)),
                int(params.get("top", 0)), int(params.get("bottom", 0)),
            ))
        else:
            self.preview_canvas.set_mode("output")

    def _crop_spin_changed(self, key: str, value: int) -> None:
        value = max(0, value) // 2 * 2
        self.candidate.params[key] = value
        params = self.candidate.params
        self.preview_canvas.set_crop_margins(
            int(params.get("left", 0)), int(params.get("right", 0)),
            int(params.get("top", 0)), int(params.get("bottom", 0)),
        )
        self._update_crop_size()

    def _crop_overlay_changed(self, left: int, right: int, top: int, bottom: int) -> None:
        if not self.candidate or self.candidate.kind != "crop":
            return
        for key, value in (("left", left), ("right", right), ("top", top), ("bottom", bottom)):
            self.candidate.params[key] = value
            spin = self._crop_spins.get(key)
            if spin and spin.value() != value:
                spin.blockSignals(True)
                spin.setValue(value)
                spin.blockSignals(False)
        self._update_crop_size()

    def _update_crop_size(self) -> None:
        if not hasattr(self, "crop_size"):
            return
        width, height = filtered_dimensions(self.prefix_filters, self.video_info.width, self.video_info.height)
        params = self.candidate.params
        width -= int(params.get("left", 0)) + int(params.get("right", 0))
        height -= int(params.get("top", 0)) + int(params.get("bottom", 0))
        self.crop_size.setText(f"{max(2, width)} × {max(2, height)}")

    def _toggle_original(self, checked: bool) -> None:
        self.preview_canvas.set_mode("input" if checked else "output")
        self.peek_button.setText("Show filtered" if checked else "Show original")

    def _step_frame(self, direction: int) -> None:
        self._stop_playback(schedule_preview=False)
        frame_ms = max(1, round(1000.0 / max(0.001, self.video_info.fps)))
        self.current_ms = max(0, min(self.video_info.duration_ms, self.current_ms + direction * frame_ms))
        self._set_seek_value(self.current_ms)
        self.time_label.setText(format_timecode(self.current_ms))
        self._schedule_preview()

    def _seek_changed(self, value: int) -> None:
        if self._updating_seek:
            return
        self._stop_playback(schedule_preview=False)
        self.current_ms = value
        self.time_label.setText(format_timecode(self.current_ms))
        self._schedule_preview()

    def _set_seek_value(self, value: int) -> None:
        self._updating_seek = True
        self.seek.setValue(value)
        self._updating_seek = False

    def _schedule_preview(self) -> None:
        if self._play_process is not None:
            return
        self._timer.start()

    def _render_preview(self) -> None:
        if self._play_process is not None:
            return
        output_filters = self.prefix_filters
        if self.candidate and self.candidate.kind != "crop":
            output_filters = output_filters + [self.candidate]
        output_graph = video_filtergraph(output_filters, self.video_info.fps, include_timing=False)
        self._stop_preview_process()
        process = QProcess(self)
        self._preview_process = process
        output_arguments = [
            "-hide_banner", "-loglevel", "error", "-ss", f"{self.current_ms / 1000:.6f}",
            "-i", str(Path(self.video_info.path)), "-frames:v", "1",
        ]
        if output_graph:
            output_arguments.extend(("-vf", output_graph))
        output_arguments.extend(("-f", "image2pipe", "-vcodec", "png", "pipe:1"))
        process.finished.connect(lambda _code, _status, owner=process: self._preview_finished(owner))
        process.start("ffmpeg", output_arguments)

        if not (self.candidate and self.candidate.kind == "crop"):
            original_filters = self.prefix_filters if self.candidate else []
            original_graph = video_filtergraph(original_filters, self.video_info.fps, include_timing=False)
            original_arguments = [
                "-hide_banner", "-loglevel", "error", "-ss", f"{self.current_ms / 1000:.6f}",
                "-i", str(Path(self.video_info.path)), "-frames:v", "1",
            ]
            if original_graph:
                original_arguments.extend(("-vf", original_graph))
            original_arguments.extend(("-f", "image2pipe", "-vcodec", "png", "pipe:1"))
            original_process = QProcess(self)
            self._original_preview_process = original_process
            original_process.finished.connect(
                lambda _code, _status, owner=original_process: self._original_preview_finished(owner)
            )
            original_process.start("ffmpeg", original_arguments)

    def _preview_finished(self, process: QProcess) -> None:
        if process is not self._preview_process:
            process.deleteLater()
            return
        data = bytes(process.readAllStandardOutput())
        pixmap = QPixmap()
        if data and pixmap.loadFromData(data, "PNG"):
            self.preview_canvas.set_output(pixmap)
            if self.candidate and self.candidate.kind == "crop":
                self.preview_canvas.set_input(pixmap)
            if self.histogram:
                self.histogram.set_pixmap(pixmap)
        process.deleteLater()
        self._preview_process = None

    def _original_preview_finished(self, process: QProcess) -> None:
        if process is not self._original_preview_process:
            process.deleteLater()
            return
        data = bytes(process.readAllStandardOutput())
        pixmap = QPixmap()
        if data and pixmap.loadFromData(data, "PNG"):
            self.preview_canvas.set_input(pixmap)
        process.deleteLater()
        self._original_preview_process = None

    def _reset(self) -> None:
        if self.candidate is None:
            return
        replacement = create_filter(self.candidate.kind, self.video_info.fps)
        defaults = replacement.params
        self.candidate.params = dict(defaults)
        for key, control in self._controls.items():
            value = defaults.get(key)
            control.blockSignals(True)
            if isinstance(control, QCheckBox):
                control.setChecked(bool(value))
            elif isinstance(control, QComboBox):
                index = control.findData(value)
                control.setCurrentIndex(max(0, index))
            elif isinstance(control, QDoubleSpinBox):
                control.setValue(float(value))
            elif isinstance(control, (QSlider, QDial)):
                if key == "contrast":
                    control.setValue(round(float(value) * 100))
                elif key == "brightness":
                    control.setValue(round(float(value) * 127))
                elif self.candidate.kind == "color_balance" and key.endswith("_angle"):
                    control.setValue(round(float(value)))
                else:
                    control.setValue(round(float(value) * 100))
            elif isinstance(control, QSpinBox):
                control.setValue(int(value))
            control.blockSignals(False)
            label = self._value_labels.get(key)
            if label is not None:
                if key == "contrast":
                    label.setText(f"Contrast: {round(float(value) * 100)}%")
                elif key == "brightness":
                    label.setText(f"Brightness: {round(float(value) * 127)}")
                else:
                    label.setText(str(round(float(value) * 100)))
        if self.candidate.kind == "crop":
            self.preview_canvas.set_crop_margins(0, 0, 0, 0)
            self._update_crop_size()
        self._schedule_preview()

    def _toggle_playback(self, selection_only: bool) -> None:
        if self._play_process is not None:
            self._stop_playback(schedule_preview=True)
            return
        if selection_only:
            if self.marker_a is None or self.marker_b is None or self.marker_b <= self.marker_a:
                return
            start_ms, end_ms = self.marker_a, self.marker_b
        else:
            start_ms = self.current_ms
            end_ms = self.video_info.duration_ms
            if start_ms >= end_ms - 1:
                start_ms = 0
        self._start_playback(start_ms, end_ms, selection_only)

    def _start_playback(self, start_ms: int, end_ms: int, selection_only: bool) -> None:
        self._timer.stop()
        self._stop_preview_process()
        self._stop_playback(schedule_preview=False)
        self._play_buffer.clear()
        self._original_play_buffer.clear()
        self._play_start_ms = max(0, start_ms)
        self._play_end_ms = min(self.video_info.duration_ms, end_ms)
        self._play_frame_index = 0
        self.current_ms = self._play_start_ms
        self._set_seek_value(self.current_ms)
        self.time_label.setText(format_timecode(self.current_ms))

        filters = self.prefix_filters
        if self.candidate and self.candidate.kind != "crop":
            filters = filters + [self.candidate]
        graph = video_filtergraph(filters, self.video_info.fps, include_timing=False)
        arguments = [
            "-hide_banner", "-loglevel", "error", "-re",
            "-ss", f"{self._play_start_ms / 1000:.6f}",
            "-i", str(Path(self.video_info.path)),
            "-t", f"{max(0.001, (self._play_end_ms - self._play_start_ms) / 1000):.6f}",
            "-an",
        ]
        if graph:
            arguments.extend(("-vf", graph))
        arguments.extend(("-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "3", "pipe:1"))
        process = QProcess(self)
        self._play_process = process
        process.readyReadStandardOutput.connect(self._read_playback_frames)
        process.finished.connect(self._playback_finished)

        if not (self.candidate and self.candidate.kind == "crop"):
            original_filters = self.prefix_filters if self.candidate else []
            original_graph = video_filtergraph(original_filters, self.video_info.fps, include_timing=False)
            original_arguments = [
                "-hide_banner", "-loglevel", "error", "-re",
                "-ss", f"{self._play_start_ms / 1000:.6f}",
                "-i", str(Path(self.video_info.path)),
                "-t", f"{max(0.001, (self._play_end_ms - self._play_start_ms) / 1000):.6f}",
                "-an",
            ]
            if original_graph:
                original_arguments.extend(("-vf", original_graph))
            original_arguments.extend(("-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "3", "pipe:1"))
            original_process = QProcess(self)
            self._original_play_process = original_process
            original_process.readyReadStandardOutput.connect(self._read_original_playback_frames)
            original_process.finished.connect(self._original_playback_finished)
            original_process.start("ffmpeg", original_arguments)
        self.play_button.setText("Ⅱ Pause")
        self.play_ab_button.setText("Ⅱ Pause A–B" if selection_only else "▶ Play A–B")
        self.previous_button.setEnabled(False)
        self.next_button.setEnabled(False)
        process.start("ffmpeg", arguments)

    def _read_playback_frames(self) -> None:
        process = self._play_process
        if process is None:
            return
        try:
            self._play_buffer.extend(bytes(process.readAllStandardOutput()))
        except RuntimeError:
            return
        for frame in self._take_jpeg_frames(self._play_buffer):
            pixmap = QPixmap()
            if not pixmap.loadFromData(frame, "JPG"):
                continue
            self.preview_canvas.set_output(pixmap)
            if self.candidate and self.candidate.kind == "crop":
                self.preview_canvas.set_input(pixmap)
            if self.histogram:
                self.histogram.set_pixmap(pixmap)
            self._play_frame_index += 1
            elapsed_ms = round(self._play_frame_index * 1000.0 / max(0.001, self.video_info.fps))
            self.current_ms = min(self._play_end_ms, self._play_start_ms + elapsed_ms)
            self._set_seek_value(self.current_ms)
            self.time_label.setText(format_timecode(self.current_ms))

    def _read_original_playback_frames(self) -> None:
        process = self._original_play_process
        if process is None:
            return
        try:
            self._original_play_buffer.extend(bytes(process.readAllStandardOutput()))
        except RuntimeError:
            return
        for frame in self._take_jpeg_frames(self._original_play_buffer):
            pixmap = QPixmap()
            if pixmap.loadFromData(frame, "JPG"):
                self.preview_canvas.set_input(pixmap)

    @staticmethod
    def _take_jpeg_frames(buffer: bytearray) -> list[bytes]:
        frames: list[bytes] = []
        while True:
            start = buffer.find(b"\xff\xd8")
            if start < 0:
                buffer.clear()
                break
            end = buffer.find(b"\xff\xd9", start + 2)
            if end < 0:
                if start:
                    del buffer[:start]
                break
            frames.append(bytes(buffer[start:end + 2]))
            del buffer[:end + 2]
        return frames

    def _original_playback_finished(self, _exit_code: int, _status: QProcess.ExitStatus) -> None:
        process = self._original_play_process
        if process is None:
            return
        self._read_original_playback_frames()
        self._original_play_process = None
        process.deleteLater()

    def _playback_finished(self, _exit_code: int, _status: QProcess.ExitStatus) -> None:
        if self._play_process is None:
            return
        self._read_playback_frames()
        process = self._play_process
        self._play_process = None
        process.deleteLater()
        self._stop_original_playback()
        self._restore_playback_controls()
        self.current_ms = min(self._play_end_ms, max(self.current_ms, self._play_start_ms))
        self._set_seek_value(self.current_ms)
        self.time_label.setText(format_timecode(self.current_ms))
        self._schedule_preview()

    def _stop_playback(self, *, schedule_preview: bool) -> None:
        process = self._play_process
        self._play_process = None
        if process is not None:
            try:
                process.readyReadStandardOutput.disconnect()
                process.finished.disconnect()
            except (RuntimeError, TypeError):
                pass
            try:
                if process.state() != QProcess.NotRunning:
                    process.kill()
                    process.waitForFinished(500)
                process.deleteLater()
            except RuntimeError:
                pass
        self._play_buffer.clear()
        self._stop_original_playback()
        self._restore_playback_controls()
        if schedule_preview:
            self._schedule_preview()

    def _restore_playback_controls(self) -> None:
        self.play_button.setText("▶ Play")
        self.play_ab_button.setText("▶ Play A–B")
        self.previous_button.setEnabled(True)
        self.next_button.setEnabled(True)
        self.peek_button.setEnabled(True)

    def _stop_original_playback(self) -> None:
        process = self._original_play_process
        self._original_play_process = None
        if process is None:
            self._original_play_buffer.clear()
            return
        try:
            process.readyReadStandardOutput.disconnect()
            process.finished.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            if process.state() != QProcess.NotRunning:
                process.kill()
                process.waitForFinished(500)
            process.deleteLater()
        except RuntimeError:
            pass
        self._original_play_buffer.clear()

    def done(self, result: int) -> None:
        self._timer.stop()
        self._stop_playback(schedule_preview=False)
        self._stop_preview_process()
        super().done(result)

    def _stop_preview_process(self) -> None:
        output_process = self._preview_process
        original_process = self._original_preview_process
        self._preview_process = None
        self._original_preview_process = None
        for process in (output_process, original_process):
            if process is None:
                continue
            try:
                process.finished.disconnect()
            except (RuntimeError, TypeError):
                pass
            try:
                if process.state() != QProcess.NotRunning:
                    process.kill()
                    process.waitForFinished(500)
                process.deleteLater()
            except RuntimeError:
                pass


class FiltersPage(QWidget):
    filtersChanged = Signal(object)
    seekRequested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.video_info: VideoInfo | None = None
        self.filter_chain: list[VideoFilter] = []
        self.current_ms = 0
        self.marker_a: int | None = None
        self.marker_b: int | None = None
        self.source_pixmap = QPixmap()
        self._editor_content: QWidget | None = None
        self._preview_process: QProcess | None = None
        self._preview_render_role = "output"
        self._crop_spins: dict[str, QSpinBox] = {}
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(140)
        self._preview_timer.timeout.connect(self._render_filtered_preview)

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 3)
        root.setSpacing(0)
        self.workspace = QStackedWidget()
        root.addWidget(self.workspace, 1)

        # The popup opens on this manager, matching Avidemux's catalog -> active
        # chain flow. Preview is deliberately absent until Configure / Preview.
        manager = QWidget()
        manager.setObjectName("filterManager")
        manager_layout = QVBoxLayout(manager)
        manager_layout.setContentsMargins(8, 7, 8, 7)
        manager_layout.setSpacing(6)
        heading = QHBoxLayout()
        heading.addWidget(self._section_title("Video Filter Manager"))
        self.chain_label = QLabel("0 active")
        self.chain_label.setObjectName("filterHint")
        heading.addStretch()
        heading.addWidget(self.chain_label)
        manager_layout.addLayout(heading)

        columns = QHBoxLayout()
        columns.setSpacing(7)
        category_column = QVBoxLayout()
        category_column.addWidget(self._section_title("Categories"))
        self.categories = QListWidget()
        self.categories.setObjectName("filterList")
        self.categories.setToolTip("Choose an Avidemux-style filter category")
        self.categories.setMinimumWidth(150)
        self.categories.setMaximumWidth(190)
        self.categories.addItems(["Transformation", "Colors"])
        self.categories.currentTextChanged.connect(self._populate_available)
        category_column.addWidget(self.categories, 1)
        columns.addLayout(category_column)

        available_column = QVBoxLayout()
        available_column.addWidget(self._section_title("Available filters"))
        self.available = QListWidget()
        self.available.setObjectName("filterList")
        self.available.setToolTip("Double-click a filter to configure and add it")
        self.available.setMinimumWidth(280)
        self.available.itemDoubleClicked.connect(lambda _item: self._add_filter())
        available_column.addWidget(self.available, 1)
        self.add_button = QPushButton("Add →")
        self.add_button.setToolTip("Configure the selected filter, then add it to the active chain")
        self.add_button.clicked.connect(self._add_filter)
        available_column.addWidget(self.add_button)
        columns.addLayout(available_column, 1)

        active_column = QVBoxLayout()
        active_column.addWidget(self._section_title("Active filters — applied top to bottom"))
        self.active = QListWidget()
        self.active.setObjectName("filterList")
        self.active.setToolTip("Active filters run from top to bottom; double-click to configure")
        self.active.setMinimumWidth(340)
        self.active.itemDoubleClicked.connect(lambda _item: self._open_filter_editor())
        active_column.addWidget(self.active, 1)
        active_actions = QHBoxLayout()
        active_actions.setSpacing(4)
        self.configure_button = QPushButton("Configure")
        self.remove_button = QPushButton("Remove")
        self.configure_button.setToolTip("Open the selected active filter's preview and settings")
        self.remove_button.setToolTip("Remove the selected filter from the active chain")
        self.up_button = QToolButton()
        self.down_button = QToolButton()
        self.up_button.setIcon(line_icon("chevron_up", size=16))
        self.down_button.setIcon(line_icon("chevron_down", size=16))
        self.up_button.setToolTip("Move filter up")
        self.down_button.setToolTip("Move filter down")
        self.configure_button.clicked.connect(self._open_filter_editor)
        self.remove_button.clicked.connect(self._remove_filter)
        self.up_button.clicked.connect(lambda: self._move_filter(-1))
        self.down_button.clicked.connect(lambda: self._move_filter(1))
        for button in (self.configure_button, self.remove_button, self.up_button, self.down_button):
            active_actions.addWidget(button)
        active_actions.addStretch()
        active_column.addLayout(active_actions)
        columns.addLayout(active_column, 1)
        manager_layout.addLayout(columns, 1)

        footer = QHBoxLayout()
        self.preview_chain_button = QPushButton("Preview active filters")
        self.preview_chain_button.setToolTip("Play and inspect the complete active filter chain")
        self.preview_chain_button.clicked.connect(self._open_chain_preview)
        footer.addStretch()
        footer.addWidget(self.preview_chain_button)
        manager_layout.addLayout(footer)
        self.workspace.addWidget(manager)

        # Configuration is a second view reached explicitly from the manager.
        editor = QWidget()
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(4)
        editor_header = QHBoxLayout()
        self.back_button = QPushButton("← Filter manager")
        self.back_button.setToolTip("Return to the filter catalog and active chain")
        self.back_button.clicked.connect(self.show_manager)
        editor_header.addWidget(self.back_button)
        self.editor_title = self._section_title("Configure filter")
        editor_header.addWidget(self.editor_title)
        editor_header.addStretch()
        editor_layout.addLayout(editor_header)

        horizontal = QSplitter(Qt.Horizontal)
        horizontal.setChildrenCollapsible(False)
        horizontal.setHandleWidth(1)
        editor_layout.addWidget(horizontal, 1)

        preview = QWidget()
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(0, 0, 4, 0)
        preview_layout.setSpacing(3)
        preview_header = QHBoxLayout()
        preview_header.setSpacing(5)
        preview_header.addWidget(self._section_title("Preview"))
        self.preview_mode = QComboBox()
        self.preview_mode.setToolTip("Choose how original and filtered frames are displayed")
        self.preview_mode.addItem("Crop editor", "crop")
        self.preview_mode.addItem("Filtered output", "output")
        self.preview_mode.addItem("Input", "input")
        self.preview_mode.addItem("Compare", "side")
        self.preview_mode.setCurrentIndex(1)
        self.preview_mode.currentIndexChanged.connect(self._preview_mode_changed)
        preview_header.addWidget(self.preview_mode)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setToolTip("Re-render the filtered frame at the current position")
        self.refresh_button.clicked.connect(self.refresh_preview)
        preview_header.addWidget(self.refresh_button)
        self.preview_status = QLabel("No active filters")
        self.preview_status.setObjectName("filterHint")
        preview_header.addWidget(self.preview_status)
        preview_header.addStretch()
        preview_layout.addLayout(preview_header)
        self.preview_canvas = FilterPreviewCanvas()
        self.preview_canvas.setToolTip("Preview active filters; drag coral handles when editing crop")
        self.preview_canvas.cropChanged.connect(self._crop_overlay_changed)
        preview_layout.addWidget(self.preview_canvas, 1)
        seek_row = QHBoxLayout()
        self.preview_time = QLabel("00:00:00.000")
        self.preview_time.setObjectName("timecode")
        self.preview_seek = QSlider(Qt.Horizontal)
        self.preview_seek.setToolTip("Seek to a different source-video frame for filter preview")
        self.preview_seek.setRange(0, 1)
        self.preview_seek.sliderReleased.connect(lambda: self.seekRequested.emit(self.preview_seek.value()))
        seek_row.addWidget(self.preview_time)
        seek_row.addWidget(self.preview_seek, 1)
        preview_layout.addLayout(seek_row)
        horizontal.addWidget(preview)

        inspector = QWidget()
        inspector.setObjectName("filterInspector")
        inspector.setMinimumWidth(320)
        inspector.setMaximumWidth(390)
        inspector_layout = QVBoxLayout(inspector)
        inspector_layout.setContentsMargins(7, 2, 7, 5)
        self.settings_group = QGroupBox("Selected filter")
        self.settings_layout = QVBoxLayout(self.settings_group)
        self.settings_layout.setContentsMargins(8, 7, 8, 7)
        inspector_layout.addWidget(self.settings_group)
        inspector_layout.addStretch(1)
        horizontal.addWidget(inspector)
        horizontal.setStretchFactor(0, 1)
        horizontal.setStretchFactor(1, 0)
        horizontal.setSizes([1190, 360])
        self.workspace.addWidget(editor)

        self.categories.setCurrentRow(0)
        self._show_empty_editor()
        self.show_manager()

    @staticmethod
    def _section_title(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    def set_source(self, info: VideoInfo, pixmap: QPixmap) -> None:
        self.video_info = info
        self.preview_seek.setRange(0, max(1, info.duration_ms))
        self.set_source_frame(pixmap, 0, refresh=False)
        self._populate_available(self.categories.currentItem().text() if self.categories.currentItem() else "Transformation")
        if self.workspace.currentIndex() == 1:
            self.refresh_preview()

    def set_source_frame(self, pixmap: QPixmap, milliseconds: int, refresh: bool = True) -> None:
        self.source_pixmap = pixmap.copy()
        self.current_ms = milliseconds
        self.preview_canvas.set_input(self.source_pixmap)
        self.preview_time.setText(format_timecode(milliseconds))
        self.preview_seek.blockSignals(True)
        self.preview_seek.setValue(milliseconds)
        self.preview_seek.blockSignals(False)
        if refresh:
            self.refresh_preview()

    def set_position(self, milliseconds: int) -> None:
        self.current_ms = milliseconds
        self.preview_time.setText(format_timecode(milliseconds))
        self.preview_seek.blockSignals(True)
        self.preview_seek.setValue(milliseconds)
        self.preview_seek.blockSignals(False)

    def set_markers(self, marker_a: int | None, marker_b: int | None) -> None:
        self.marker_a = marker_a
        self.marker_b = marker_b

    def set_filters(self, filters: list[VideoFilter]) -> None:
        self.filter_chain = [VideoFilter.from_dict(item.to_dict()) for item in filters]
        self._rebuild_active_list()
        if self.workspace.currentIndex() == 1:
            self.refresh_preview()

    def filters(self) -> list[VideoFilter]:
        return [VideoFilter.from_dict(item.to_dict()) for item in self.filter_chain]

    def refresh_preview(self) -> None:
        if self.workspace.currentIndex() != 1:
            return
        self.preview_canvas.set_input(self.source_pixmap)
        mode = str(self.preview_mode.currentData())
        self.preview_canvas.set_mode(mode)
        if mode == "input":
            self.preview_status.setText("Original input frame")
            return
        if not self.filter_chain:
            self.preview_canvas.set_output(self.source_pixmap)
            self.preview_status.setText("No active filters")
            return
        if mode == "crop" and self._selected_crop() is not None:
            self.preview_status.setText("Preparing crop editor…")
        else:
            self.preview_status.setText(f"Rendering {len(self.filter_chain)} active filter(s)…")
        self._preview_timer.start()

    def _preview_mode_changed(self) -> None:
        self.preview_canvas.set_mode(str(self.preview_mode.currentData()))
        self.refresh_preview()

    def show_manager(self) -> None:
        if hasattr(self, "workspace"):
            self.workspace.setCurrentIndex(0)
        self._preview_timer.stop()
        self.close_preview_process()
        window = self.window()
        if window is not self:
            window.resize(1000, 600)

    def _open_filter_editor(self) -> None:
        row = self.active.currentRow()
        if not self.video_info or not 0 <= row < len(self.filter_chain):
            return
        dialog = FilterConfigDialog(
            self.video_info,
            self.source_pixmap,
            self.current_ms,
            self.filter_chain[:row],
            self.filter_chain[row],
            self.window(),
            marker_a=self.marker_a,
            marker_b=self.marker_b,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        configured = dialog.result_filter()
        if configured is None:
            return
        self.filter_chain[row] = configured
        self._rebuild_active_list(select=row)
        self._changed(refresh=False)

    def _open_chain_preview(self) -> None:
        if not self.video_info or not self.filter_chain:
            return
        FilterConfigDialog(
            self.video_info,
            self.source_pixmap,
            self.current_ms,
            self.filter_chain,
            None,
            self.window(),
            marker_a=self.marker_a,
            marker_b=self.marker_b,
        ).exec()

    def _populate_available(self, category: str) -> None:
        self.available.clear()
        for kind, definition in FILTER_CATALOG.items():
            if definition["category"] != category:
                continue
            item = QListWidgetItem(str(definition["name"]))
            item.setData(Qt.UserRole, kind)
            item.setToolTip(str(definition["description"]))
            self.available.addItem(item)
        if self.available.count():
            self.available.setCurrentRow(0)

    def _add_filter(self) -> None:
        item = self.available.currentItem()
        if item is None or not self.video_info:
            return
        candidate = create_filter(str(item.data(Qt.UserRole)), self.video_info.fps)
        dialog = FilterConfigDialog(
            self.video_info,
            self.source_pixmap,
            self.current_ms,
            self.filter_chain,
            candidate,
            self.window(),
            marker_a=self.marker_a,
            marker_b=self.marker_b,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        configured = dialog.result_filter()
        if configured is None:
            return
        self.filter_chain.append(configured)
        self._rebuild_active_list(select=len(self.filter_chain) - 1)
        self._changed(refresh=False)

    def _remove_filter(self) -> None:
        row = self.active.currentRow()
        if not 0 <= row < len(self.filter_chain):
            return
        self.filter_chain.pop(row)
        self._rebuild_active_list(select=min(row, len(self.filter_chain) - 1))
        self._changed()

    def _move_filter(self, offset: int) -> None:
        row = self.active.currentRow()
        target = row + offset
        if not (0 <= row < len(self.filter_chain) and 0 <= target < len(self.filter_chain)):
            return
        self.filter_chain[row], self.filter_chain[target] = self.filter_chain[target], self.filter_chain[row]
        self._rebuild_active_list(select=target)
        self._changed()

    def _rebuild_active_list(self, select: int | None = None) -> None:
        if select is None:
            select = self.active.currentRow()
        self.active.blockSignals(True)
        self.active.clear()
        for item in self.filter_chain:
            row = QListWidgetItem(describe_filter(item))
            row.setToolTip(str(FILTER_CATALOG[item.kind]["description"]))
            self.active.addItem(row)
        self.active.blockSignals(False)
        count = len(self.filter_chain)
        self.chain_label.setText(f"{count} active")
        self.preview_chain_button.setEnabled(bool(count))
        self.configure_button.setEnabled(bool(count))
        self.remove_button.setEnabled(bool(count))
        self.up_button.setEnabled(count > 1)
        self.down_button.setEnabled(count > 1)
        if self.filter_chain:
            self.active.setCurrentRow(max(0, min(select, len(self.filter_chain) - 1)))
        else:
            self._show_empty_editor()

    def _show_filter_editor(self, row: int) -> None:
        if not 0 <= row < len(self.filter_chain):
            self._show_empty_editor()
            return
        item = self.filter_chain[row]
        self._crop_spins = {}
        content = QWidget()
        form = QFormLayout(content)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        description = QLabel(str(FILTER_CATALOG[item.kind]["description"]))
        description.setWordWrap(True)
        description.setObjectName("filterHint")
        form.addRow(description)

        if item.kind == "crop":
            stage_width, stage_height = self._stage_dimensions(row)
            for label, key in (("Left", "left"), ("Right", "right"), ("Top", "top"), ("Bottom", "bottom")):
                spin = QSpinBox()
                maximum = stage_width if key in ("left", "right") else stage_height
                spin.setRange(0, max(0, maximum - 2))
                spin.setSingleStep(2)
                spin.setValue(int(item.params.get(key, 0)))
                spin.valueChanged.connect(lambda value, k=key, control=spin: self._set_crop(k, value, control))
                form.addRow(f"{label} (px)", spin)
                self._crop_spins[key] = spin
            self.crop_output = QLabel()
            self.crop_output.setObjectName("cropOutput")
            form.addRow("Output", self.crop_output)
            self._update_crop_output_label()
            reset = QPushButton("Reset crop")
            reset.clicked.connect(self._reset_crop)
            form.addRow(reset)
            note = QLabel("Drag the coral edge or corner handles on the preview. Drag inside the frame to move it. Values stay even for encoder compatibility.")
            note.setObjectName("filterHint")
            note.setWordWrap(True)
            form.addRow(note)
        elif item.kind == "rotate":
            angle = QComboBox()
            for value in (90, 180, 270):
                angle.addItem(f"{value}°", value)
            angle.setCurrentIndex(max(0, angle.findData(int(item.params.get("angle", 90)))))
            angle.currentIndexChanged.connect(lambda: self._set_param("angle", int(angle.currentData())))
            form.addRow("Angle", angle)
        elif item.kind == "vertical_flip":
            form.addRow(QLabel("No additional settings."))
        elif item.kind == "frame_rate":
            mode = QComboBox()
            mode.addItem("Resample — keep duration", "resample")
            mode.addItem("Change speed — slow/fast motion", "speed")
            mode.setCurrentIndex(max(0, mode.findData(item.params.get("mode", "resample"))))
            fps = QDoubleSpinBox()
            fps.setRange(1.0, 240.0)
            fps.setDecimals(3)
            fps.setSingleStep(1.0)
            fps.setSuffix(" fps")
            fps.setValue(float(item.params.get("fps", self.video_info.fps if self.video_info else 25.0)))
            blend = QCheckBox("Blend interpolated frames")
            blend.setChecked(bool(item.params.get("blend", False)))
            mode.currentIndexChanged.connect(lambda: self._set_param("mode", str(mode.currentData())))
            mode.currentIndexChanged.connect(lambda: blend.setEnabled(mode.currentData() == "resample"))
            fps.valueChanged.connect(lambda value: self._set_param("fps", float(value)))
            blend.toggled.connect(lambda checked: self._set_param("blend", checked))
            blend.setEnabled(mode.currentData() == "resample")
            form.addRow("Behavior", mode)
            form.addRow("Output rate", fps)
            form.addRow("Interpolation", blend)
            source = self.video_info.fps if self.video_info else 0.0
            explanation = QLabel(
                f"Source: {source:.3f} fps. Resample keeps duration; Change speed changes video and audio duration."
            )
            explanation.setObjectName("filterHint")
            explanation.setWordWrap(True)
            form.addRow(explanation)
        elif item.kind == "contrast":
            contrast = QDoubleSpinBox()
            contrast.setRange(0.0, 3.0)
            contrast.setDecimals(2)
            contrast.setSingleStep(0.05)
            contrast.setValue(float(item.params.get("contrast", 1.0)))
            brightness = QDoubleSpinBox()
            brightness.setRange(-1.0, 1.0)
            brightness.setDecimals(2)
            brightness.setSingleStep(0.05)
            brightness.setValue(float(item.params.get("brightness", 0.0)))
            contrast.valueChanged.connect(lambda value: self._set_param("contrast", float(value)))
            brightness.valueChanged.connect(lambda value: self._set_param("brightness", float(value)))
            form.addRow("Contrast", contrast)
            form.addRow("Brightness", brightness)
        self._replace_editor(content)
        if self.workspace.currentIndex() == 1:
            self._configure_preview_for_selection()

    def _show_empty_editor(self) -> None:
        message = QLabel("Add or select an active filter to configure it.")
        message.setWordWrap(True)
        message.setObjectName("filterHint")
        self._replace_editor(message)
        if hasattr(self, "preview_canvas"):
            self.preview_canvas.set_crop_editor(False)
            crop_option = self.preview_mode.model().item(0)
            if crop_option is not None:
                crop_option.setEnabled(False)
            if self.preview_mode.currentData() == "crop":
                self.preview_mode.setCurrentIndex(1)

    def _replace_editor(self, widget: QWidget) -> None:
        while self.settings_layout.count():
            child = self.settings_layout.takeAt(0)
            if child.widget() is not None:
                child.widget().deleteLater()
        self._editor_content = widget
        self.settings_layout.addWidget(widget)
        self.settings_layout.addStretch(1)

    def _set_crop(self, key: str, value: int, control: QSpinBox) -> None:
        row = self.active.currentRow()
        if not 0 <= row < len(self.filter_chain):
            return
        item = self.filter_chain[row]
        value = max(0, int(value)) // 2 * 2
        other_key = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}[key]
        stage_width, stage_height = self._stage_dimensions(row)
        dimension = stage_width if key in ("left", "right") else stage_height
        value = min(value, max(0, dimension - int(item.params.get(other_key, 0)) - 2)) // 2 * 2
        if control.value() != value:
            control.blockSignals(True)
            control.setValue(value)
            control.blockSignals(False)
        item.params[key] = value
        self._update_active_text(row)
        self.preview_canvas.set_crop_margins(
            int(item.params.get("left", 0)), int(item.params.get("right", 0)),
            int(item.params.get("top", 0)), int(item.params.get("bottom", 0)),
        )
        self._update_crop_output_label()
        self._changed(refresh=self.preview_mode.currentData() != "crop")

    def _set_param(self, key: str, value) -> None:
        row = self.active.currentRow()
        if not 0 <= row < len(self.filter_chain):
            return
        self.filter_chain[row].params[key] = value
        self._update_active_text(row)
        self._changed()

    def _update_active_text(self, row: int) -> None:
        item = self.active.item(row)
        if item is not None:
            item.setText(describe_filter(self.filter_chain[row]))

    def _changed(self, refresh: bool = True) -> None:
        self.filtersChanged.emit(self.filters())
        if refresh and self.workspace.currentIndex() == 1:
            self.refresh_preview()

    def _stage_dimensions(self, row: int) -> tuple[int, int]:
        if self.video_info is None:
            return 8192, 8192
        return filtered_dimensions(self.filter_chain[:max(0, row)], self.video_info.width, self.video_info.height)

    def _selected_crop(self) -> tuple[int, VideoFilter] | None:
        row = self.active.currentRow()
        if 0 <= row < len(self.filter_chain) and self.filter_chain[row].kind == "crop":
            return row, self.filter_chain[row]
        return None

    def _configure_preview_for_selection(self) -> None:
        selected = self._selected_crop()
        crop_option = self.preview_mode.model().item(0)
        if crop_option is not None:
            crop_option.setEnabled(selected is not None)
        if selected is None:
            self.preview_canvas.set_crop_editor(False)
            if self.preview_mode.currentData() == "crop":
                self.preview_mode.setCurrentIndex(1)
            return
        row, item = selected
        width, height = self._stage_dimensions(row)
        self.preview_canvas.set_crop_editor(
            True, width, height,
            (
                int(item.params.get("left", 0)), int(item.params.get("right", 0)),
                int(item.params.get("top", 0)), int(item.params.get("bottom", 0)),
            ),
        )
        if self.preview_mode.currentData() != "crop":
            self.preview_mode.setCurrentIndex(0)
        else:
            self.refresh_preview()

    def _crop_overlay_changed(self, left: int, right: int, top: int, bottom: int) -> None:
        selected = self._selected_crop()
        if selected is None:
            return
        row, item = selected
        for key, value in (("left", left), ("right", right), ("top", top), ("bottom", bottom)):
            item.params[key] = value
            control = self._crop_spins.get(key)
            if control is not None and control.value() != value:
                control.blockSignals(True)
                control.setValue(value)
                control.blockSignals(False)
        self.preview_canvas.set_crop_margins(left, right, top, bottom)
        self._update_active_text(row)
        self._update_crop_output_label()
        self.preview_status.setText("Crop updated · drag handles or choose Filtered output to inspect the result")
        self._changed(refresh=False)

    def _update_crop_output_label(self) -> None:
        selected = self._selected_crop()
        if selected is None or not hasattr(self, "crop_output"):
            return
        row, item = selected
        width, height = self._stage_dimensions(row)
        output_width = max(2, width - int(item.params.get("left", 0)) - int(item.params.get("right", 0)))
        output_height = max(2, height - int(item.params.get("top", 0)) - int(item.params.get("bottom", 0)))
        self.crop_output.setText(f"{output_width} × {output_height}")

    def _reset_crop(self) -> None:
        self._crop_overlay_changed(0, 0, 0, 0)
        self.preview_canvas.set_crop_margins(0, 0, 0, 0)

    def _render_filtered_preview(self) -> None:
        if self.video_info is None or not self.filter_chain:
            return
        render_filters = self.filter_chain
        self._preview_render_role = "output"
        if self.preview_mode.currentData() == "crop":
            selected = self._selected_crop()
            if selected is not None:
                row, _item = selected
                render_filters = self.filter_chain[:row]
                self._preview_render_role = "crop"
        graph = video_filtergraph(render_filters, self.video_info.fps, include_timing=False)
        if not graph:
            self.preview_canvas.set_output(self.source_pixmap)
            if self._preview_render_role == "crop":
                self.preview_status.setText("Drag the coral handles to set the crop area")
            else:
                self.preview_status.setText("Preview updated · timing-only filters do not change this frame")
            return
        if self._preview_process is not None and self._preview_process.state() != QProcess.NotRunning:
            self._preview_process.kill()
        process = QProcess(self)
        self._preview_process = process
        arguments = [
            "-hide_banner", "-loglevel", "error", "-ss", f"{self.current_ms / 1000:.6f}",
            "-i", str(Path(self.video_info.path)), "-frames:v", "1", "-vf", graph,
            "-f", "image2pipe", "-vcodec", "png", "pipe:1",
        ]
        process.finished.connect(lambda _code, _status, owner=process: self._preview_finished(owner))
        process.start("ffmpeg", arguments)

    def _preview_finished(self, process: QProcess) -> None:
        if process is not self._preview_process:
            process.deleteLater()
            return
        data = bytes(process.readAllStandardOutput())
        pixmap = QPixmap()
        if data and pixmap.loadFromData(data, "PNG"):
            self.preview_canvas.set_output(pixmap)
            if self._preview_render_role == "crop":
                self.preview_status.setText("Drag the coral handles to set the crop area")
            else:
                self.preview_status.setText(f"{len(self.filter_chain)} active filter(s) · preview updated")
        else:
            detail = bytes(process.readAllStandardError()).decode("utf-8", errors="replace").strip()
            self.preview_status.setText(detail.splitlines()[-1] if detail else "Preview failed")
        process.deleteLater()
        self._preview_process = None

    def close_preview_process(self) -> None:
        if self._preview_process is not None and self._preview_process.state() != QProcess.NotRunning:
            self._preview_process.kill()
            self._preview_process.waitForFinished(1000)
