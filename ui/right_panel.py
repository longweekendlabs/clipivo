from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QEvent, QModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
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
from .styles import PALETTE


THUMBNAIL_ROLE = Qt.UserRole + 1
TIME_ROLE = Qt.UserRole + 2
ORIGIN_ROLE = Qt.UserRole + 3
CLIP_ROLE = Qt.UserRole + 4


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
            return clip.origin.capitalize()
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
    exportRequested = Signal(str, bool, bool)
    addRangeRequested = Signal()
    collapseRequested = Signal()
    targetRequested = Signal()
    findCharacterRequested = Signal()
    findAllRequested = Signal()

    def __init__(self, project_dir: Path, parent=None):
        super().__init__(parent)
        self.project_dir = project_dir
        self.setObjectName("rightPanel")
        self.setMinimumWidth(264)
        self.setMaximumWidth(310)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(5)

        target_header = QHBoxLayout()
        self.target_title = QLabel("No target selected")
        self.target_title.setObjectName("sectionTitle")
        self.target_title.setToolTip("Character selected for person analysis")
        self.change_target_button = QToolButton()
        self.change_target_button.setText("Choose")
        self.change_target_button.setObjectName("addRange")
        self.change_target_button.setToolTip("Open Characters and choose a reusable or ad-hoc target")
        self.change_target_button.clicked.connect(self.targetRequested.emit)
        target_header.addWidget(self.target_title)
        target_header.addStretch()
        target_header.addWidget(self.change_target_button)
        layout.addLayout(target_header)

        self.target_scope = QLabel("Reusable characters work across videos; ad-hoc targets stay with one video.")
        self.target_scope.setObjectName("targetHint")
        self.target_scope.setWordWrap(True)
        self.target_scope.setToolTip("Target character scope")
        layout.addWidget(self.target_scope)
        self.target_actions_widget = QWidget()
        target_actions = QHBoxLayout(self.target_actions_widget)
        target_actions.setContentsMargins(0, 0, 0, 0)
        target_actions.setSpacing(4)
        self.find_character_button = QPushButton("Find character")
        self.find_character_button.setObjectName("targetAction")
        self.find_character_button.setEnabled(False)
        self.find_character_button.setToolTip(
            "Scan the complete video and jump to the first frame matching this character; successful results are cached"
        )
        self._find_next_available = False
        self.find_character_button.clicked.connect(self.findCharacterRequested.emit)
        target_actions.addWidget(self.find_character_button)
        self.find_all_button = QPushButton("Find all")
        self.find_all_button.setObjectName("targetAction")
        self.find_all_button.setEnabled(False)
        self.find_all_button.setToolTip(
            "Scan the complete video and turn every appearance of this character into a clip (Ctrl+Shift+F)"
        )
        self.find_all_button.clicked.connect(self.findAllRequested.emit)
        target_actions.addWidget(self.find_all_button)
        self.target_actions_widget.hide()
        layout.addWidget(self.target_actions_widget)
        self.target_thumbnails = QWidget()
        thumbnail_layout = QHBoxLayout(self.target_thumbnails)
        thumbnail_layout.setContentsMargins(0, 0, 0, 0)
        thumbnail_layout.setSpacing(4)
        self.target_thumbnail_labels: list[QLabel] = []
        for index in range(4):
            label = QLabel()
            label.setObjectName("targetThumbnail")
            label.setFixedSize(58, 58)
            label.setAlignment(Qt.AlignCenter)
            label.setToolTip(f"Target reference {index + 1}")
            thumbnail_layout.addWidget(label)
            self.target_thumbnail_labels.append(label)
        thumbnail_layout.addStretch()
        self.target_thumbnails.hide()
        layout.addWidget(self.target_thumbnails)

        clips_header = QHBoxLayout()
        clips_header.setContentsMargins(0, 0, 0, 0)
        clips_header.setSpacing(5)
        self.collapse_button = QToolButton()
        self.collapse_button.setObjectName("paneToggle")
        self.collapse_button.setIcon(line_icon("chevron_right", size=18))
        self.collapse_button.setIconSize(QSize(18, 18))
        self.collapse_button.setToolTip("Collapse clip pane")
        self.collapse_button.clicked.connect(self.collapseRequested.emit)
        self.clip_title = QLabel("Clips (0)")
        self.clip_title.setObjectName("sectionTitle")
        self.add_range_button = QToolButton()
        self.add_range_button.setObjectName("addRange")
        self.add_range_button.setText("Add A–B")
        self.add_range_button.setToolTip("Add the current A–B selection to the clip list (Ctrl+Enter)")
        self.add_range_button.clicked.connect(self.addRangeRequested.emit)
        clips_header.addWidget(self.collapse_button)
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
        self.clips_view.setToolTip("Enabled clip ranges to export; click a row to restore its A/B markers")
        self.clips_view.clicked.connect(lambda index: self.clipActivated.emit(index.data(CLIP_ROLE)))
        layout.addWidget(self.clips_view, 1)

        export_options = QVBoxLayout()
        export_options.setSpacing(4)
        self.output = QComboBox()
        self.output.addItem("Copy MP4", "copy")
        self.output.addItem("Exact-cut MP4", "accurate")
        self.output.setItemData(
            0,
            "No re-encoding: preserves the source video quality, but non-keyframe starts may shift.",
            Qt.ToolTipRole,
        )
        self.output.setItemData(
            1,
            "Re-encodes to H.264/AAC: slower, but follows the selected A/B frames exactly.",
            Qt.ToolTipRole,
        )
        self.output.setCurrentIndex(1)
        self.output_hint = QLabel()
        self.output_hint.setWordWrap(True)
        self.output_hint.setStyleSheet(f"color: {PALETTE['muted']}; font-size: 10px;")
        self.output.currentIndexChanged.connect(self._update_output_hint)

        self.arrangement = QComboBox()
        self.arrangement.addItem("Separate", "separate")
        self.arrangement.addItem("Stitched", "stitched")
        self.arrangement.setToolTip("Export each enabled range separately or join them into one file.")

        selectors = QHBoxLayout()
        selectors.setSpacing(4)
        self.output.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.arrangement.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        selectors.addWidget(self.output, 1)
        selectors.addWidget(self.arrangement, 1)

        footer = QHBoxLayout()
        footer.setSpacing(5)
        self.include_audio = QCheckBox("Include audio")
        self.include_audio.setChecked(True)
        self.include_audio.setToolTip("Uncheck to create exported clips without an audio track.")
        self.save_button = QPushButton("Save clips")
        self.save_button.setObjectName("primary")
        self.save_button.setToolTip("Export all enabled clip ranges using the selected output settings")
        self.save_button.clicked.connect(self._emit_export)
        footer.addWidget(self.include_audio)
        footer.addStretch(1)
        footer.addWidget(self.save_button)

        export_options.addLayout(selectors)
        export_options.addLayout(footer)
        layout.addLayout(export_options)
        self._update_output_hint()

    def set_clips(self, clips: list[ClipRange]) -> None:
        self.clip_model.set_clips(clips)
        self._update_count()

    def set_target(
        self,
        name: str,
        scope: str,
        thumbnails: list[QPixmap],
    ) -> None:
        self.target_title.setText(f"Target: {name}")
        self.target_title.setToolTip(f"Current person-analysis target: {name}")
        scope_text = "Ad-hoc · this video only" if scope == "adhoc" else "Reusable character"
        self._target_scope_text = scope_text
        self.target_scope.setText(f"{scope_text} · ready to scan")
        self.set_find_next_available(False)
        self.find_character_button.setEnabled(True)
        self.find_all_button.setEnabled(True)
        self.target_actions_widget.show()
        for index, label in enumerate(self.target_thumbnail_labels):
            if index < len(thumbnails):
                label.setPixmap(thumbnails[index].scaled(label.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
                label.setToolTip(f"Reference {index + 1} for {name}")
                label.show()
            else:
                label.clear()
                label.hide()
        self.target_thumbnails.setVisible(bool(thumbnails))

    def set_find_running(self, running: bool) -> None:
        if running:
            self.find_character_button.setText("Finding next…" if self._find_next_available else "Finding…")
        else:
            self.find_character_button.setText("Find next" if self._find_next_available else "Find character")
        self.find_character_button.setEnabled(not running)
        self.find_all_button.setEnabled(not running)
        scope_text = getattr(self, "_target_scope_text", "Selected character")
        if running:
            self.target_scope.setText(f"{scope_text} · scanning video")
        else:
            self.target_scope.setText(f"{scope_text} · ready to scan")

    def set_find_result(self, text: str, *, find_next: bool = False) -> None:
        scope_text = getattr(self, "_target_scope_text", "Selected character")
        self.target_scope.setText(f"{scope_text} · {text}")
        self.set_find_next_available(find_next)

    def set_find_next_available(self, available: bool) -> None:
        self._find_next_available = bool(available)
        self.find_character_button.setText("Find next" if available else "Find character")
        if available:
            self.find_character_button.setToolTip(
                "Find the next distinct appearance after the character has left the video for a confirmed gap"
            )
        else:
            self.find_character_button.setToolTip(
                "Scan the complete video and jump to the first frame matching this character; successful results are cached"
            )

    def clear_target(self) -> None:
        self.target_title.setText("No target selected")
        self.target_title.setToolTip("No character is selected for person analysis")
        self._target_scope_text = ""
        self.target_scope.setText("Reusable characters work across videos; ad-hoc targets stay with one video.")
        self.find_character_button.setEnabled(False)
        self.find_all_button.setEnabled(False)
        self.set_find_next_available(False)
        self.target_actions_widget.hide()
        self.target_thumbnails.hide()

    def set_find_all_running(self, running: bool) -> None:
        self.find_all_button.setText("Finding all…" if running else "Find all")
        self.find_all_button.setEnabled(not running)
        self.find_character_button.setEnabled(not running)
        scope_text = getattr(self, "_target_scope_text", "Selected character")
        self.target_scope.setText(
            f"{scope_text} · finding every appearance" if running else f"{scope_text} · ready to scan"
        )

    def select_stitched_output(self) -> None:
        self.arrangement.setCurrentIndex(max(0, self.arrangement.findData("stitched")))

    def select_default_output_mode(self) -> None:
        index = self.output.findData("accurate")
        self.output.setCurrentIndex(max(0, index))

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
        model_item = self.output.model().item(0) if hasattr(self.output.model(), "item") else None
        if model_item is not None:
            model_item.setEnabled(not active)
        if active:
            self.output.setCurrentIndex(1)
            self.output.setToolTip("Active video filters require re-encoding; Copy MP4 is unavailable.")
        else:
            self._update_output_hint()

    def _update_count(self) -> None:
        self.clip_title.setText(f"Clips ({len(self.clip_model.clips)})")

    def _update_output_hint(self, _index: int = 0) -> None:
        description = str(self.output.currentData(Qt.ToolTipRole) or "")
        self.output.setToolTip(description)
        if self.output.currentData() == "copy":
            self.output_hint.setText("Source quality, no re-encode; starts may shift to keyframes.")
        else:
            self.output_hint.setText("Exact A/B frames; re-encodes to H.264/AAC.")

    def _emit_export(self) -> None:
        self.exportRequested.emit(
            str(self.output.currentData()),
            self.arrangement.currentData() == "stitched",
            self.include_audio.isChecked(),
        )
