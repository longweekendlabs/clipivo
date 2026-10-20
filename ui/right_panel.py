from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QEvent, QModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListView,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.models import ClipRange
from core.timecode import format_timecode

from .icons import line_icon
from .job_panel import JobPanel
from .styles import PALETTE


THUMBNAIL_ROLE = Qt.UserRole + 1
TIME_ROLE = Qt.UserRole + 2
ORIGIN_ROLE = Qt.UserRole + 3
CLIP_ROLE = Qt.UserRole + 4
ORIGIN_LABELS = {"automatic": "Found", "automatic_uncertain": "Found · unsure", "manual": "Manual"}


class ClipListModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.clips: list[ClipRange] = []
        self.thumbnails: dict[str, QPixmap] = {}

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.clips)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.clips)):
            return None
        clip = self.clips[index.row()]
        if role == Qt.DisplayRole:
            return str(index.row() + 1)
        if role == TIME_ROLE:
            return f"{format_timecode(clip.start_ms)} – {format_timecode(clip.end_ms)}"
        if role == ORIGIN_ROLE:
            return ORIGIN_LABELS.get(clip.origin, clip.origin.capitalize())
        if role == THUMBNAIL_ROLE:
            return self.thumbnails.get(clip.id)
        if role == CLIP_ROLE:
            return clip
        if role == Qt.CheckStateRole:
            return Qt.Checked if clip.enabled else Qt.Unchecked
        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable

    def setData(self, index: QModelIndex, value, role=Qt.EditRole) -> bool:
        if role != Qt.CheckStateRole or not index.isValid():
            return False
        self.clips[index.row()].enabled = value == Qt.Checked
        self.dataChanged.emit(index, index, [Qt.CheckStateRole])
        return True

    def set_clips(self, clips: list[ClipRange]) -> None:
        self.beginResetModel()
        self.clips = clips
        self.thumbnails = {key: value for key, value in self.thumbnails.items() if any(item.id == key for item in clips)}
        self.endResetModel()

    def add_clip(self, clip: ClipRange, thumbnail: QPixmap | None = None) -> QModelIndex:
        row = len(self.clips)
        self.beginInsertRows(QModelIndex(), row, row)
        self.clips.append(clip)
        if thumbnail is not None:
            self.thumbnails[clip.id] = thumbnail
        self.endInsertRows()
        return self.index(row, 0)

    def remove_row(self, row: int) -> ClipRange | None:
        if not 0 <= row < len(self.clips):
            return None
        self.beginRemoveRows(QModelIndex(), row, row)
        clip = self.clips.pop(row)
        self.thumbnails.pop(clip.id, None)
        self.endRemoveRows()
        return clip


class ClipDelegate(QStyledItemDelegate):
    def sizeHint(self, _option, _index) -> QSize:
        return QSize(246, 56)

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = option.rect.adjusted(4, 5, -3, -5)
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor("#252b33"))

        check = QStyleOptionButton()
        check.rect = self._check_rect(option.rect)
        check.state = QStyle.State_Enabled | (
            QStyle.State_On if index.data(Qt.CheckStateRole) == Qt.Checked else QStyle.State_Off
        )
        QApplication.style().drawControl(QStyle.CE_CheckBox, check, painter)

        thumb_rect = QRect(rect.left() + 21, rect.top(), 64, 40)
        thumb = index.data(THUMBNAIL_ROLE)
        painter.fillRect(thumb_rect, QColor("#101318"))
        if isinstance(thumb, QPixmap):
            scaled = thumb.scaled(thumb_rect.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            painter.drawPixmap(thumb_rect, scaled)
        painter.setPen(QPen(QColor(PALETTE["border"]), 1))
        painter.drawRoundedRect(thumb_rect, 3, 3)

        text_x = thumb_rect.right() + 7
        painter.setPen(QColor(PALETTE["text"]))
        painter.drawText(
            QRect(text_x, rect.top() - 1, rect.width() - 112, 18),
            Qt.AlignLeft | Qt.AlignVCenter,
            f"{index.data(Qt.DisplayRole)}  {index.data(ORIGIN_ROLE)}",
        )
        painter.setPen(QColor(PALETTE["muted"]))
        time_font = painter.font()
        time_font.setPixelSize(10)
        painter.setFont(time_font)
        painter.drawText(
            QRect(text_x, rect.top() + 20, rect.width() - 96, 16),
            Qt.AlignLeft | Qt.AlignVCenter,
            index.data(TIME_ROLE),
        )
        painter.restore()

    def editorEvent(self, event, model, option, index: QModelIndex) -> bool:
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            if self._check_rect(option.rect).contains(event.position().toPoint()):
                current = index.data(Qt.CheckStateRole)
                return model.setData(index, Qt.Unchecked if current == Qt.Checked else Qt.Checked, Qt.CheckStateRole)
        return super().editorEvent(event, model, option, index)

    @staticmethod
    def _check_rect(rect: QRect) -> QRect:
        return QRect(rect.left() + 6, rect.center().y() - 8, 16, 16)


class RightPanel(QWidget):
    clipActivated = Signal(object)
    saveClipsRequested = Signal()
    addRangeRequested = Signal()
    collapseRequested = Signal()

    def __init__(self, project_dir: Path, job: JobPanel, parent=None):
        super().__init__(parent)
        self.project_dir = project_dir
        self.job = job
        self.setObjectName("rightPanel")
        self.setMinimumWidth(264)
        self.setMaximumWidth(310)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(5)

        self.collapse_button = QToolButton()
        self.collapse_button.setObjectName("paneToggle")
        self.collapse_button.setIcon(line_icon("chevron_right", size=18))
        self.collapse_button.setIconSize(QSize(18, 18))
        self.collapse_button.setToolTip("Collapse this pane")
        self.collapse_button.clicked.connect(self.collapseRequested.emit)
        job.who_header.addWidget(self.collapse_button)
        layout.addWidget(job)

        clips_header = QHBoxLayout()
        clips_header.setContentsMargins(0, 6, 0, 0)
        clips_header.setSpacing(5)
        self.clip_title = QLabel("Clips (0)")
        self.clip_title.setObjectName("sectionTitle")
        self.add_range_button = QToolButton()
        self.add_range_button.setObjectName("addRange")
        self.add_range_button.setText("Add A–B")
        self.add_range_button.setToolTip("Add the current A–B selection to the clip list (Ctrl+Enter)")
        self.add_range_button.clicked.connect(self.addRangeRequested.emit)
        clips_header.addWidget(self.clip_title)
        clips_header.addStretch()
        clips_header.addWidget(self.add_range_button)
        layout.addLayout(clips_header)

        self.clips_view = QListView()
        self.clips_view.setVerticalScrollMode(QListView.ScrollPerPixel)
        self.clip_model = ClipListModel(self.clips_view)
        self.clips_view.setModel(self.clip_model)
        self.clips_view.setItemDelegate(ClipDelegate(self.clips_view))
        self.clips_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.clips_view.setToolTip("Clips found by Start or marked with A–B; click one to jump there, untick to leave it out")
        self.clips_view.clicked.connect(lambda index: self.clipActivated.emit(index.data(CLIP_ROLE)))
        layout.addWidget(self.clips_view, 1)

        footer = QHBoxLayout()
        footer.setSpacing(5)
        self.clips_hint = QLabel("Start fills this list. Tweak clips, then save them again.")
        self.clips_hint.setObjectName("targetHint")
        self.clips_hint.setWordWrap(True)
        footer.addWidget(self.clips_hint, 1)
        self.save_button = QPushButton("Save clips")
        self.save_button.setToolTip("Save the ticked clips with the Output settings above")
        self.save_button.clicked.connect(self.saveClipsRequested.emit)
        footer.addWidget(self.save_button)
        layout.addLayout(footer)

    def set_clips(self, clips: list[ClipRange]) -> None:
        self.clip_model.set_clips(clips)
        self._update_count()

    def add_clip(self, clip: ClipRange, thumbnail: QPixmap | None = None) -> None:
        index = self.clip_model.add_clip(clip, thumbnail)
        self.clips_view.setCurrentIndex(index)
        self.clips_view.scrollTo(index)
        self._update_count()

    def selected_clip(self) -> ClipRange | None:
        index = self.clips_view.currentIndex()
        return index.data(CLIP_ROLE) if index.isValid() else None

    def remove_selected(self) -> ClipRange | None:
        index = self.clips_view.currentIndex()
        removed = self.clip_model.remove_row(index.row()) if index.isValid() else None
        self._update_count()
        return removed

    def ranges(self) -> list[ClipRange]:
        return self.clip_model.clips

    def set_filters_active(self, active: bool) -> None:
        self.job.set_copy_allowed(not active)

    def _update_count(self) -> None:
        self.clip_title.setText(f"Clips ({len(self.clip_model.clips)})")
