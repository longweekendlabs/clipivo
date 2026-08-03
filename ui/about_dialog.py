from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from version import __app_name__, __copyright__, __org_name__, __tagline__, __version__

from .styles import PALETTE


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {__app_name__}")
        self.setModal(True)
        self.setFixedWidth(470)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 20)
        layout.setSpacing(10)

        mark = QLabel()
        mark.setPixmap(self._lwl_mark(52))
        mark.setAlignment(Qt.AlignHCenter)
        mark.setToolTip("Long Weekend Labs")
        layout.addWidget(mark)

        name = QLabel(__app_name__)
        name.setAlignment(Qt.AlignHCenter)
        font = QFont("Inter", 20)
        font.setBold(True)
        name.setFont(font)
        name.setStyleSheet(f"color: {PALETTE['accent']};")
        layout.addWidget(name)

        version = QLabel(f"Version {__version__}  ·  {__tagline__}")
        version.setAlignment(Qt.AlignHCenter)
        version.setStyleSheet(f"color: {PALETTE['muted']};")
        version.setToolTip("Clipivo application version")
        layout.addWidget(version)

        description = QLabel(
            "A local-first Avidemux-style video cutter with reusable and per-video "
            "character references. Face processing stays on this computer."
        )
        description.setWordWrap(True)
        description.setAlignment(Qt.AlignHCenter)
        description.setStyleSheet(f"color: {PALETTE['secondary']};")
        layout.addWidget(description)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setToolTip("About section divider")
        layout.addWidget(divider)

        notice = QLabel(
            "Private development build. The bundled InsightFace buffalo_l model has "
            "research-use licensing constraints and is not approved for commercial distribution."
        )
        notice.setWordWrap(True)
        notice.setAlignment(Qt.AlignHCenter)
        notice.setStyleSheet(f"color: {PALETTE['muted']}; font-size: 10px;")
        notice.setToolTip("Face-model licensing notice")
        layout.addWidget(notice)

        org = QLabel(f"Made with <span style='color:{PALETTE['accent']}'>♥</span> by {__org_name__}<br>{__copyright__}")
        org.setTextFormat(Qt.RichText)
        org.setAlignment(Qt.AlignHCenter)
        org.setStyleSheet(f"color: {PALETTE['muted']};")
        org.setToolTip("Clipivo is a Long Weekend Labs project")
        layout.addWidget(org)

        close_button = QPushButton("Close")
        close_button.setToolTip("Close the About window")
        close_button.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(close_button)
        row.addStretch()
        layout.addLayout(row)

    @staticmethod
    def _lwl_mark(size: int) -> QPixmap:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(PALETTE["accent"]), 1.6))
        painter.setBrush(QColor("#241917"))
        painter.drawRoundedRect(QRectF(1, 1, size - 2, size - 2), 11, 11)
        painter.setPen(QColor(PALETTE["accent"]))
        font = QFont("Inter")
        font.setBold(True)
        font.setPixelSize(round(size * 0.32))
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "LWL")
        painter.end()
        return pixmap

