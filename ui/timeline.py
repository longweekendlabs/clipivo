from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider

from .styles import PALETTE


class DetectionTimeline(QSlider):
    """Single-track timeline used by the visual shell.

    Phase 0 intentionally paints mock values only. It becomes the real seek and
    A/B selection control after visual approval.
    """

    scrubStarted = Signal()
    scrubFinished = Signal(int)

    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.setRange(0, 1000)
        self.setValue(0)
        self.setFixedHeight(42)
        self.setMouseTracking(True)
        self.detections: list[int] = []
        self.marker_a: int | None = None
        self.marker_b: int | None = None
        self._scrubbing = False

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        left, right = 3.0, self.width() - 3.0
        track_y = 27.0
        track = QRectF(left, track_y, right - left, 7.0)

        painter.setPen(QPen(QColor(PALETTE["border"]), 1))
        painter.setBrush(QColor("#252b32"))
        painter.drawRoundedRect(track, 2, 2)

        if self.marker_a is not None and self.marker_b is not None:
            a_x = self._x(self.marker_a)
            b_x = self._x(self.marker_b)
            painter.fillRect(QRectF(a_x, track_y + 2, max(1, b_x - a_x), 3), QColor("#727981"))

        painter.setPen(QPen(QColor(PALETTE["accent"]), 2))
        for value in self.detections:
            x = self._x(value)
            painter.drawLine(QPointF(x, track_y - 1), QPointF(x, track_y + 9))

        if self.marker_a is not None and self.marker_b is not None and self.marker_a == self.marker_b:
            self._paint_marker(painter, self._x(self.marker_a), "A/B", width=30)
        else:
            if self.marker_a is not None:
                self._paint_marker(painter, self._x(self.marker_a), "A")
            if self.marker_b is not None:
                self._paint_marker(painter, self._x(self.marker_b), "B")

        handle_x = self._x(self.value())
        painter.setPen(QPen(QColor("#dce1e7"), 1))
        painter.setBrush(QColor("#e7eaee"))
        painter.drawRoundedRect(QRectF(handle_x - 3, track_y - 1, 6, 11), 2, 2)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        self._scrubbing = True
        self.scrubStarted.emit()
        self._set_from_x(event.position().x())
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.LeftButton:
            self._set_from_x(event.position().x())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._scrubbing:
            self._set_from_x(event.position().x())
            self._scrubbing = False
            self.scrubFinished.emit(self.value())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _set_from_x(self, x: float) -> None:
        span = max(1.0, self.width() - 6.0)
        value = round((max(3.0, min(x, self.width() - 3.0)) - 3.0) / span * self.maximum())
        self.setValue(value)
        self.update()

    def _x(self, value: int) -> float:
        return 3.0 + (self.width() - 6.0) * value / max(1, self.maximum())

    def _paint_marker(self, painter: QPainter, x: float, text: str, *, width: int = 16) -> None:
        y = 1.0
        half = width / 2
        path = QPainterPath()
        path.addPolygon(QPolygonF([
            QPointF(x - half, y), QPointF(x + half, y), QPointF(x + half, y + 16),
            QPointF(x + 3, y + 16), QPointF(x, y + 21), QPointF(x - 3, y + 16),
            QPointF(x - half, y + 16),
        ]))
        path.closeSubpath()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(PALETTE["accent"]))
        painter.drawPath(path)
        painter.setPen(QColor(PALETTE["accent_text"]))
        font = QFont("Inter", 9, QFont.Bold)
        painter.setFont(font)
        painter.drawText(QRectF(x - half, y, width, 16), Qt.AlignCenter, text)
