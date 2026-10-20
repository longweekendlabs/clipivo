from __future__ import annotations

import bisect
from pathlib import Path
from threading import Event

from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QImage, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QSlider,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTabBar,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.export import ExportError, date_tag, export_ranges
from core.character_store import CharacterRecord, CharacterStore
from core.face_engine import FaceEngine
from core.filters import VideoFilter
from core.models import ClipRange, VideoInfo
from core.project_state import (
    load_find_result,
    load_filters,
    load_ranges,
    load_target_selection,
    project_id,
    save_filters,
    save_find_result,
    save_ranges,
    save_target_selection,
)
from core.timecode import format_timecode
from core.video_controller import DecodeError, DecodedFrame, VideoController
from core.video_analysis import AppearanceMatch, find_end_of_appearance, find_first_appearance
from core.video_probe import ProbeError, probe_video

from .about_dialog import AboutDialog
from .batch_page import BatchPage
from core.batch import BatchCharacterTarget, analyse_video_for_characters
from .icons import line_icon
from .filters_page import FiltersPage
from .native_dialogs import choose_directory, choose_png_file, choose_save_file, choose_video
from .people_page import PeoplePage
from .right_panel import RightPanel
from .settings_page import SettingsPage
from .styles import PALETTE, app_stylesheet
from .timeline import DetectionTimeline
from .vlc_player import VlcPlaybackController
from version import __app_name__, __org_name__, __version__


class ProbeWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self) -> None:
        try:
            self.succeeded.emit(probe_video(self.path, include_keyframes=True))
        except (ProbeError, OSError, ValueError) as exc:
            self.failed.emit(str(exc))


class ExportWorker(QThread):
    progressed = Signal(int, int, str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        source: Path,
        clips: list[ClipRange],
        output_dir: Path,
        mode: str,
        stitched: bool,
        include_audio: bool,
        target: Path | None,
        filters: list[VideoFilter],
        source_fps: float,
        video_encoder: str,
        parent=None,
    ):
        super().__init__(parent)
        self.source = source
        self.clips = [ClipRange.from_dict(item.to_dict()) for item in clips]
        self.output_dir = output_dir
        self.mode = mode
        self.stitched = stitched
        self.include_audio = include_audio
        self.target = target
        self.filters = [VideoFilter.from_dict(item.to_dict()) for item in filters]
        self.source_fps = source_fps
        self.video_encoder = video_encoder
        self.cancel_event = Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        try:
            outputs = export_ranges(
                source=self.source,
                ranges=self.clips,
                output_dir=self.output_dir,
                mode=self.mode,
                stitched=self.stitched,
                include_audio=self.include_audio,
                progress=lambda done, total, text: self.progressed.emit(done, total, text),
                cancel_event=self.cancel_event,
                target=self.target,
                filters=self.filters,
                source_fps=self.source_fps,
                video_encoder=self.video_encoder,
            )
            self.succeeded.emit(outputs)
        except ExportError as exc:
            self.failed.emit(str(exc))


class FindCharacterWorker(QThread):
    progressed = Signal(int, int, str)
    found = Signal(object)
    notFound = Signal()
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        source: Path,
        start_ms: int,
        end_ms: int,
        fast_face_engine: FaceEngine,
        accurate_face_engine: FaceEngine,
        references: list[tuple[str, bytes]],
        threshold: float,
        previous_match_ms: int | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.source = source
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.fast_face_engine = fast_face_engine
        self.accurate_face_engine = accurate_face_engine
        self.references = references
        self.threshold = threshold
        self.previous_match_ms = previous_match_ms

    def cancel(self) -> None:
        self.requestInterruption()

    def run(self) -> None:
        try:
            # The first pass is deliberately cheap. A miss there is not a
            # verdict: sparse 320px keyframes can overlook small faces or a
            # brief shot. Escalate automatically to configured-resolution
            # the configured detector, first sparsely and then at the original
            # reliable cadence, before emitting notFound.
            search_start_ms = self.start_ms
            if self.previous_match_ms is not None:
                departure = find_end_of_appearance(
                    self.source,
                    self.accurate_face_engine,
                    self.references,
                    self.threshold,
                    start_ms=self.previous_match_ms,
                    end_ms=self.end_ms,
                    sample_interval_ms=750,
                    absence_confirm_ms=1500,
                    cancelled=self.isInterruptionRequested,
                    progress=lambda current, total: self.progressed.emit(
                        current, total, "Locating end of current appearance"
                    ),
                )
                if departure is None or self.isInterruptionRequested():
                    if self.isInterruptionRequested():
                        self.cancelled.emit()
                    else:
                        self.notFound.emit()
                    return
                search_start_ms = departure
            passes = (
                ("Rapid keyframe pass", self.fast_face_engine, 10_000, True),
                ("High-resolution keyframe pass", self.accurate_face_engine, 5_000, True),
                ("Careful pass", self.accurate_face_engine, 750, False),
            )
            result = None
            for label, engine, interval_ms, keyframes_only in passes:
                if self.isInterruptionRequested():
                    break
                result = find_first_appearance(
                    self.source,
                    engine,
                    self.references,
                    self.threshold,
                    duration_ms=self.end_ms,
                    start_ms=search_start_ms,
                    end_ms=self.end_ms,
                    sample_interval_ms=interval_ms,
                    refine_interval_ms=250,
                    keyframes_only=keyframes_only,
                    cancelled=self.isInterruptionRequested,
                    progress=lambda current, total, phase=label: self.progressed.emit(current, total, phase),
                )
                if result is not None:
                    break
            if self.isInterruptionRequested():
                self.cancelled.emit()
            elif result is None:
                self.notFound.emit()
            else:
                self.found.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class FindAllWorker(QThread):
    progressed = Signal(int, int, str)
    finishedRanges = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        source: Path,
        duration_ms: int,
        face_engine: FaceEngine,
        target: BatchCharacterTarget,
        parent=None,
    ):
        super().__init__(parent)
        self.source = source
        self.duration_ms = duration_ms
        self.face_engine = face_engine
        self.target = target

    def cancel(self) -> None:
        self.requestInterruption()

    def run(self) -> None:
        try:
            results = analyse_video_for_characters(
                self.source,
                self.face_engine,
                [self.target],
                duration_ms=self.duration_ms,
                quality="balanced",
                cancelled=self.isInterruptionRequested,
                progress=lambda current, total, _label: self.progressed.emit(
                    current, total, "Scanning the whole video"
                ),
                refine_progress=lambda done, total: self.progressed.emit(
                    done, max(1, total), f"Tightening clip edges {done + 1} of {total}"
                ),
            )
            if self.isInterruptionRequested():
                self.cancelled.emit()
                return
            self.finishedRanges.emit(list(results[0].ranges) if results else [])
        except Exception as exc:  # noqa: BLE001
            if self.isInterruptionRequested():
                self.cancelled.emit()
            else:
                self.failed.emit(str(exc))


class VideoPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("videoSurface")
        self.source = QPixmap()
        self._scaled = QPixmap()
        self._scaled_size = QSize()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(640, 360)

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self.source = pixmap
        self._scaled = QPixmap()
        self._scaled_size = QSize()
        self.update()

    def clear(self) -> None:
        self.source = QPixmap()
        self._scaled = QPixmap()
        self._scaled_size = QSize()
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#080a0d"))
        if self.source.isNull():
            painter.setPen(QColor(PALETTE["secondary"]))
            font = QFont("Inter")
            font.setPixelSize(17)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(self.rect().adjusted(0, -18, 0, 0), Qt.AlignCenter, "Open a video to begin")
            font.setPixelSize(12)
            font.setBold(False)
            painter.setFont(font)
            painter.setPen(QColor(PALETTE["muted"]))
            painter.drawText(self.rect().adjusted(0, 28, 0, 0), Qt.AlignCenter, "MP4, MKV, WebM, MOV, AVI or M4V")
            return
        if self._scaled.isNull() or self._scaled_size != self.size():
            self._scaled = self.source.scaled(self.size(), Qt.KeepAspectRatio, Qt.FastTransformation)
            self._scaled_size = self.size()
        x = (self.width() - self._scaled.width()) // 2
        y = (self.height() - self._scaled.height()) // 2
        painter.drawPixmap(x, y, self._scaled)


class PlaceholderPage(QWidget):
    def __init__(self, title: str, text: str, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        heading = QLabel(title)
        heading.setObjectName("placeholderTitle")
        body = QLabel(text)
        body.setObjectName("placeholderBody")
        body.setAlignment(Qt.AlignCenter)
        layout.addWidget(heading, 0, Qt.AlignHCenter)
        layout.addWidget(body, 0, Qt.AlignHCenter)


class MarkerButton(QToolButton):
    oppositeRequested = Signal()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.oppositeRequested.emit()
        super().mouseDoubleClickEvent(event)


class MainWindow(QMainWindow):
    def __init__(
        self,
        project_dir: Path,
        character_store: CharacterStore | None = None,
        face_engine: FaceEngine | None = None,
        store_error: str = "",
    ):
        super().__init__()
        self.project_dir = project_dir
        self.character_store = character_store
        self.face_engine = face_engine
        self.store_error = store_error
        self.current_target: CharacterRecord | None = None
        self.detected_appearances: list[int] = []
        self.video_info: VideoInfo | None = None
        self.controller: VideoController | None = None
        self.current_ms = 0
        self.marker_a: int | None = None
        self.marker_b: int | None = None
        self._updating_timeline = False
        self._timeline_scrubbing = False
        self._manual_frame = True
        self.selection_play_end_ms: int | None = None
        self.playback_muted = False
        self.probe_worker: ProbeWorker | None = None
        self.export_worker: ExportWorker | None = None
        self.export_progress: QProgressDialog | None = None
        self.find_worker: FindCharacterWorker | None = None
        self.find_progress: QProgressDialog | None = None
        self.find_all_worker: FindAllWorker | None = None
        self.find_all_progress: QProgressDialog | None = None
        self.search_face_engine: FaceEngine | None = None
        self.find_all_face_engine: FaceEngine | None = None
        self._find_start_ms = 0
        self._find_end_ms = 0
        self._find_fingerprint = ""
        self._find_is_next = False
        self.export_video_encoder = "auto"
        self._pending_batch_review: tuple[Path, str, str, list[ClipRange]] | None = None

        self.player = VlcPlaybackController(self)
        self.player.positionChanged.connect(self._on_player_position)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.errorOccurred.connect(self._on_player_error)

        self.setWindowTitle(f"{__app_name__} v{__version__}")
        self.setMinimumSize(1100, 700)
        self.resize(1584, 940)
        self.setStyleSheet(app_stylesheet())
        self._build_actions()
        self._build_menu()
        self._build_toolbar()
        self._build_center()
        self._build_status()
        self._set_video_controls_enabled(False)

    def _build_actions(self) -> None:
        self.actions: dict[str, QAction] = {}

        def create(key: str, text: str, slot, shortcut: str | None = None, tooltip: str = "") -> QAction:
            action = QAction(text, self)
            action.triggered.connect(slot)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
                action.setShortcutContext(Qt.ApplicationShortcut)
            action.setToolTip(tooltip or text.replace("…", ""))
            action.setStatusTip(tooltip or text.replace("…", ""))
            self.addAction(action)
            self.actions[key] = action
            return action

        create("open", "Open video…", self._choose_video, "Ctrl+O")
        create("export", "Save clips…", self._request_export_selected_mode, "Ctrl+S")
        screenshot = create(
            "screenshot",
            "Save current frame as PNG…",
            self._save_current_frame,
            "Ctrl+P",
            "Save the currently displayed source-resolution video frame as a PNG (Ctrl+P)",
        )
        screenshot.setEnabled(False)
        create("close_window", "Close window", self.close, "Ctrl+W", "Close the Clipivo window (Ctrl+W)")
        create("exit", "Exit", self.close, "Ctrl+Q")
        create("marker_a", "Set marker A", self._set_marker_a, "A")
        create("marker_b", "Set marker B", self._set_marker_b, "B")
        create("add_range", "Add A–B range", self._add_manual_range, "Ctrl+Return")
        create("remove_clip", "Remove selected clip", self._remove_selected_clip, "Delete")
        create("collapse", "Collapse right pane", self._collapse_right)
        create("start", "Jump to start", lambda: self._seek(0), "Home")
        create("end", "Jump to end", self._jump_to_end, "End")
        create("previous_frame", "Previous frame", lambda: self._step_frame(False), "Left")
        create("next_frame", "Next frame", lambda: self._step_frame(True), "Right")
        create("previous_keyframe", "Previous keyframe", lambda: self._step_keyframe(False), "Down")
        create("next_keyframe", "Next keyframe", lambda: self._step_keyframe(True), "Up")
        create("play", "Play / Pause", self._toggle_playback, "Space")
        create("play_selection", "Play A–B selection", self._play_selection, "Shift+Space")
        create("stop", "Stop", self._stop, "K")
        find_character = create(
            "find_character",
            "Find character",
            self._find_character,
            "Ctrl+F",
            "Scan the complete video and jump to the first matching frame; successful results are cached",
        )
        find_character.setEnabled(False)
        find_all = create(
            "find_all",
            "Find all appearances",
            self._find_all,
            "Ctrl+Shift+F",
            "Scan the complete video and turn every appearance of the target into a clip",
        )
        find_all.setEnabled(False)
        create("video_page", "Video", lambda: self._select_page(0))
        create("filters_page", "Filters", lambda: self._select_page(1))
        create("batch_page", "Batch", lambda: self._select_page(2))
        create("people_page", "People", lambda: self._select_page(3))
        create("settings", "Settings", lambda: self._select_page(4))
        self.page_action_group = QActionGroup(self)
        self.page_action_group.setExclusive(True)
        for key in ("video_page", "filters_page", "batch_page", "people_page", "settings"):
            self.actions[key].setCheckable(True)
            self.page_action_group.addAction(self.actions[key])
        self.actions["video_page"].setChecked(True)
        create("about", "About Clipivo", self._show_about)

    def _build_menu(self) -> None:
        menus = {
            "File": ["open", "export", "screenshot", "close_window", "exit"],
            "Edit": ["marker_a", "marker_b", "add_range", "remove_clip"],
            "View": ["collapse", "start", "end"],
            "Video": ["play", "play_selection", "stop", "previous_frame", "next_frame", "previous_keyframe", "next_keyframe", "find_character", "find_all"],
            "Tools": ["filters_page", "batch_page", "settings"],
            "Help": ["about"],
        }
        for title, keys in menus.items():
            menu = self.menuBar().addMenu(title)
            for key in keys:
                menu.addAction(self.actions[key])

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main toolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(19, 19))
        toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.actions["open"].setIcon(line_icon("open", size=19))
        self.actions["export"].setIcon(line_icon("save", size=19))
        self.actions["video_page"].setIcon(line_icon("film", PALETTE["accent"], 19))
        self.actions["filters_page"].setIcon(line_icon("filters", size=19))
        self.actions["batch_page"].setIcon(line_icon("batch", size=19))
        self.actions["people_page"].setIcon(line_icon("person", size=19))
        self.actions["settings"].setIcon(line_icon("settings", size=19))
        toolbar.addAction(self.actions["open"])
        toolbar.addAction(self.actions["export"])
        toolbar.addSeparator()
        self.page_tabs = QTabBar()
        self.page_tabs.setObjectName("pageTabs")
        self.page_tabs.setDocumentMode(True)
        self.page_tabs.setExpanding(False)
        self.page_tabs.addTab(self.actions["video_page"].icon(), "Video")
        self.page_tabs.addTab(self.actions["filters_page"].icon(), "Filters")
        self.page_tabs.addTab(self.actions["batch_page"].icon(), "Batch")
        self.page_tabs.addTab(self.actions["people_page"].icon(), "People")
        self.page_tabs.addTab(self.actions["settings"].icon(), "Settings")
        for index, tip in enumerate((
            "Manual video playback, A/B marking, clips and export",
            "Manage the active video-filter chain",
            "Queue one or many videos for automatic character clipping",
            "Manage reusable and per-video character references",
            "Configure local recognition and view application information",
        )):
            self.page_tabs.setTabToolTip(index, tip)
        self.page_tabs.setToolTip("Switch Clipivo workspace")
        self.page_tabs.currentChanged.connect(self._select_page)
        toolbar.addWidget(self.page_tabs)
        toolbar.addSeparator()
        self.source_label = QLabel("No video loaded")
        self.source_label.setStyleSheet(f"color: {PALETTE['secondary']}; padding-left: 4px;")
        self.source_label.setToolTip("Currently imported source video")
        toolbar.addWidget(self.source_label)
        self.addToolBar(Qt.TopToolBarArea, toolbar)

    def _build_center(self) -> None:
        self.pages = QStackedWidget()
        self.pages.addWidget(self._video_page())
        self.people_page = PeoplePage(
            self.character_store,
            self.face_engine,
            self.store_error,
            frame_provider=lambda: self.preview.source.copy(),
        )
        self.people_page.targetActivated.connect(self._target_activated)
        self.people_page.referencesChanged.connect(self._target_references_changed)
        self.people_page.characterDeleted.connect(self._target_character_deleted)
        self.batch_page = BatchPage(self.character_store, self.face_engine)
        self.batch_page.openVideoRequested.connect(self._batch_open_video_requested)
        self.batch_page.reviewRequested.connect(self._batch_review_requested)
        self.batch_page.approveReviewRequested.connect(self._batch_review_approved)
        self.batch_page.set_match_threshold(self.people_page.match_threshold)
        self.pages.addWidget(self.batch_page)
        self.pages.addWidget(self.people_page)
        self.settings_page = SettingsPage(self.face_engine)
        self.settings_page.faceSettingsChanged.connect(self._face_settings_changed)
        self.settings_page.exportSettingsChanged.connect(self._export_settings_changed)
        self.settings_page.aboutRequested.connect(self._show_about)
        use_gpu, detector_size, threshold = self.settings_page.values()
        self._face_settings_changed(use_gpu, detector_size, threshold)
        self._export_settings_changed(self.settings_page.export_encoder_value())
        self.pages.addWidget(self.settings_page)
        self.setCentralWidget(self.pages)

        self._content_tab = 0
        self.filters_dialog = QDialog(self)
        self.filters_dialog.setObjectName("filtersDialog")
        self.filters_dialog.setWindowTitle("Clipivo — Video Filters")
        self.filters_dialog.setModal(False)
        self.filters_dialog.setMinimumSize(1050, 680)
        self.filters_dialog.resize(1000, 600)
        dialog_layout = QVBoxLayout(self.filters_dialog)
        dialog_layout.setContentsMargins(0, 0, 0, 6)
        dialog_layout.setSpacing(4)
        self.filters_page = FiltersPage()
        self.filters_page.filtersChanged.connect(self._filters_changed)
        self.filters_page.seekRequested.connect(self._seek)
        dialog_layout.addWidget(self.filters_page, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.setToolTip("Close the filter manager; active filters remain attached to this video")
        buttons.rejected.connect(self.filters_dialog.reject)
        dialog_layout.addWidget(buttons)
        self.filters_dialog.finished.connect(self._filters_dialog_closed)

    def _select_page(self, index: int) -> None:
        if not hasattr(self, "pages"):
            return
        if index == 1:
            self._open_filters_dialog()
            return
        stack_index = {0: 0, 2: 1, 3: 2, 4: 3}.get(index)
        if stack_index is None:
            return
        if hasattr(self, "filters_dialog") and self.filters_dialog.isVisible():
            self.filters_dialog.hide()
        self._content_tab = index
        self.pages.setCurrentIndex(stack_index)
        if index == 2:
            self.batch_page.refresh_characters()
        if hasattr(self, "page_tabs") and self.page_tabs.currentIndex() != index:
            self.page_tabs.blockSignals(True)
            self.page_tabs.setCurrentIndex(index)
            self.page_tabs.blockSignals(False)
        page_keys = {0: "video_page", 2: "batch_page", 3: "people_page", 4: "settings"}
        self.actions[page_keys[index]].setChecked(True)

    def _open_filters_dialog(self) -> None:
        if not hasattr(self, "filters_dialog") or not self.video_info:
            return
        self.actions["filters_page"].setChecked(True)
        if self.page_tabs.currentIndex() != 1:
            self.page_tabs.blockSignals(True)
            self.page_tabs.setCurrentIndex(1)
            self.page_tabs.blockSignals(False)
        self.filters_page.show_manager()
        self.filters_page.set_source_frame(self.preview.source, self.current_ms, refresh=False)
        self.filters_dialog.show()
        self.filters_dialog.raise_()
        self.filters_dialog.activateWindow()

    def _filters_dialog_closed(self) -> None:
        index = self._content_tab
        if hasattr(self, "page_tabs"):
            self.page_tabs.blockSignals(True)
            self.page_tabs.setCurrentIndex(index)
            self.page_tabs.blockSignals(False)
        page_keys = {0: "video_page", 2: "batch_page", 3: "people_page", 4: "settings"}
        self.actions[page_keys[index]].setChecked(True)

    def _video_page(self) -> QWidget:
        page = QWidget()
        page_layout = QHBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(1)
        page_layout.addWidget(splitter)

        editor = QWidget()
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(4, 4, 4, 0)
        editor_layout.setSpacing(0)
        self.preview = VideoPreview()
        self.vlc_surface = QWidget()
        self.vlc_surface.setObjectName("videoSurface")
        self.vlc_surface.setStyleSheet("background: #050607;")
        self.video_stack = QStackedWidget()
        self.video_stack.addWidget(self.preview)
        self.video_stack.addWidget(self.vlc_surface)
        self.video_stack.setCurrentWidget(self.preview)
        editor_layout.addWidget(self.video_stack, 1)
        self.player.setVideoOutput(self.vlc_surface)
        self.timeline = DetectionTimeline()
        self.timeline.setToolTip(
            "Drag for coalesced native video previews; release to synchronize the exact final frame"
        )
        self.timeline.valueChanged.connect(self._on_timeline_changed)
        self.timeline.scrubStarted.connect(self._on_scrub_started)
        self.timeline.scrubFinished.connect(self._on_scrub_finished)
        editor_layout.addWidget(self.timeline)
        editor_layout.addWidget(self._transport_bar())
        splitter.addWidget(editor)

        self.video_splitter = splitter
        self.side_container = QWidget()
        self.side_container.setObjectName("sideContainer")
        side_layout = QVBoxLayout(self.side_container)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(0)

        self.restore_pane_button = QToolButton()
        self.restore_pane_button.setObjectName("paneRestore")
        self.restore_pane_button.setIcon(line_icon("chevron_left", size=18))
        self.restore_pane_button.setIconSize(QSize(18, 18))
        self.restore_pane_button.setToolTip("Restore clip pane")
        self.restore_pane_button.clicked.connect(self._restore_right)
        self.restore_pane_button.hide()
        side_layout.addWidget(self.restore_pane_button, 0, Qt.AlignTop | Qt.AlignHCenter)

        self.right_panel = RightPanel(self.project_dir)
        self.right_panel.collapseRequested.connect(self._collapse_right)
        self.right_panel.clipActivated.connect(self._activate_clip)
        self.right_panel.exportRequested.connect(self._request_export)
        self.right_panel.addRangeRequested.connect(self._add_manual_range)
        self.right_panel.targetRequested.connect(lambda: self._select_page(3))
        self.right_panel.findCharacterRequested.connect(self._find_character)
        self.right_panel.findAllRequested.connect(self._find_all)
        self.right_panel.clip_model.dataChanged.connect(lambda *_args: self._persist_ranges())
        side_layout.addWidget(self.right_panel, 1)
        splitter.addWidget(self.side_container)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        self.side_container.setMinimumWidth(264)
        self.side_container.setMaximumWidth(310)
        splitter.setSizes([1264, 272])
        return page

    def _transport_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("transportBar")
        bar.setFixedHeight(52)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(4)
        definitions = (
            ("previous", "Jump to start", "start"),
            ("back", "Previous keyframe", "previous_keyframe"),
            ("frame_back", "Previous frame", "previous_frame"),
            ("play", "Play", "play"),
            ("stop", "Stop", "stop"),
            ("frame_forward", "Next frame", "next_frame"),
            ("forward", "Next keyframe", "next_keyframe"),
            ("next", "Jump to end", "end"),
        )
        self.transport_buttons: list[QToolButton] = []
        self.transport_by_action: dict[str, QToolButton] = {}
        for kind, tooltip, action_key in definitions:
            action = self.actions[action_key]
            button = QToolButton()
            button.setObjectName("transport")
            button.setIcon(line_icon(kind, size=17))
            button.setIconSize(QSize(17, 17))
            button.setToolTip(f"{tooltip} ({action.shortcut().toString()})")
            button.clicked.connect(action.trigger)
            self.transport_buttons.append(button)
            self.transport_by_action[action_key] = button
            layout.addWidget(button)
        self.play_button = self.transport_by_action["play"]

        self.marker_buttons: list[MarkerButton] = []
        for marker, key in (("A", "marker_a"), ("B", "marker_b")):
            button = MarkerButton()
            button.setObjectName("marker")
            button.setText(marker)
            opposite = "B" if marker == "A" else "A"
            button.setToolTip(f"Set marker {marker}; double-click to also set {opposite}")
            button.clicked.connect(self.actions[key].trigger)
            button.oppositeRequested.connect(self.actions["marker_b" if marker == "A" else "marker_a"].trigger)
            self.marker_buttons.append(button)
            layout.addWidget(button)

        self.play_selection_button = QToolButton()
        self.play_selection_button.setObjectName("transport")
        self.play_selection_button.setIcon(line_icon("selection_play", size=17))
        self.play_selection_button.setIconSize(QSize(17, 17))
        self.play_selection_button.setToolTip("Play A–B selection (Shift+Space)")
        self.play_selection_button.clicked.connect(self.actions["play_selection"].trigger)
        self.transport_buttons.append(self.play_selection_button)
        self.transport_by_action["play_selection"] = self.play_selection_button
        layout.addWidget(self.play_selection_button)

        layout.addSpacing(3)
        self.timecode_label = QLabel("00:00:00.000  /  00:00:00.000")
        self.timecode_label.setObjectName("timecode")
        layout.addWidget(self.timecode_label)
        layout.addStretch(1)
        self.marker_a_label = QLabel("A:  --:--:--.---")
        self.marker_b_label = QLabel("B:  --:--:--.---")
        for marker_label in (self.marker_a_label, self.marker_b_label):
            marker_label.setObjectName("markerTime")
            layout.addWidget(marker_label)
            layout.addSpacing(3)

        self.volume_button = QToolButton()
        self.volume_button.setObjectName("volume")
        self.volume_button.setIcon(line_icon("volume", size=19))
        self.volume_button.setIconSize(QSize(19, 19))
        self.volume_button.setToolTip("Mute preview audio")
        self.volume_button.clicked.connect(self._toggle_playback_mute)
        layout.addWidget(self.volume_button)
        self.volume_slider = QSlider(Qt.Vertical)
        self.volume_slider.setObjectName("volume")
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setFixedSize(16, 40)
        self.volume_slider.setToolTip("Preview volume")
        self.volume_slider.valueChanged.connect(self._set_playback_volume)
        layout.addWidget(self.volume_slider)
        return bar

    def _build_status(self) -> None:
        status = QStatusBar()
        status.setFixedHeight(24)
        self.status_text = QLabel("  No video loaded    •   ")
        status.addWidget(self.status_text)
        self.ready_text = QLabel("Ready")
        self.ready_text.setStyleSheet(f"color: {PALETTE['ready']};")
        status.addWidget(self.ready_text)
        brand = QLabel(f"{__app_name__} v{__version__}  ·  {__org_name__}  ")
        brand.setStyleSheet(f"color: {PALETTE['muted']};")
        brand.setToolTip("Clipivo version and Long Weekend Labs branding")
        status.addPermanentWidget(brand)
        self.setStatusBar(status)

    def _choose_video(self) -> None:
        filename = choose_video(self)
        if filename:
            self.open_video(Path(filename))

    def open_video(self, path: Path) -> None:
        if self.probe_worker and self.probe_worker.isRunning():
            return
        if self.find_worker and self.find_worker.isRunning():
            self.find_worker.cancel()
            self.ready_text.setText("Cancelling character scan…")
            return
        if self.find_all_worker and self.find_all_worker.isRunning():
            self.find_all_worker.cancel()
            self.ready_text.setText("Cancelling character scan…")
            return
        self._stop(decode_frame=False)
        self._clear_current_target()
        self._set_video_controls_enabled(False)
        self.ready_text.setText("Reading video…")
        self.status_text.setText(f"  {path.name}    •   indexing metadata and keyframes    •   ")
        self.probe_worker = ProbeWorker(path, self)
        self.probe_worker.succeeded.connect(self._video_opened)
        self.probe_worker.failed.connect(self._video_open_failed)
        self.probe_worker.start()

    def _video_opened(self, info: VideoInfo) -> None:
        try:
            controller = VideoController(info.path)
            first = controller.first_frame()
        except DecodeError as exc:
            self._video_open_failed(str(exc))
            return
        if self.controller:
            self.controller.close()
        self.video_info = info
        self.controller = controller
        self.current_ms = first.timestamp_ms
        self.marker_a = None
        self.marker_b = None
        self.timeline.setRange(0, max(1, info.duration_ms))
        self.timeline.marker_a = None
        self.timeline.marker_b = None
        self.timeline.detections = []
        self._show_decoded_frame(first)
        self._set_position(first.timestamp_ms, update_player=False)
        self.source_label.setText(info.path.name)
        self.player.setSource(info.path)
        self.right_panel.set_clips(load_ranges(info.path))
        self.right_panel.select_default_output_mode()
        self.filters_page.set_filters(load_filters(info.path))
        self.filters_page.set_source(info, self.preview.source)
        self._filters_changed(self.filters_page.filters(), persist=False)
        saved_target = load_target_selection(info.path)
        self.people_page.set_video_context(project_id(info.path), info.path.name, saved_target.get("character_id", ""))
        selected = self.people_page.selected_character()
        if selected and selected.id == saved_target.get("character_id"):
            if not selected.is_adhoc or selected.project_id == project_id(info.path):
                self._set_current_target(selected, persist=False)
            else:
                self._clear_current_target()
        else:
            self._clear_current_target()
        self._update_marker_labels()
        self._set_video_controls_enabled(True)
        self._set_status_for_info(info)
        self.ready_text.setText("Ready")
        self._apply_pending_batch_review()

    def _batch_open_video_requested(self, source: Path) -> None:
        self._pending_batch_review = None
        self._select_page(0)
        if self.video_info and self.video_info.path.resolve() == Path(source).resolve():
            return
        self.open_video(Path(source))

    def _batch_review_requested(
        self,
        source: Path,
        character_id: str,
        character_name: str,
        ranges: list[ClipRange],
    ) -> None:
        self._pending_batch_review = (
            Path(source),
            character_id,
            character_name,
            [ClipRange.from_dict(item.to_dict()) for item in ranges],
        )
        self._select_page(0)
        if self.video_info and self.video_info.path.resolve() == Path(source).resolve():
            self._apply_pending_batch_review()
        else:
            self.open_video(Path(source))

    def _apply_pending_batch_review(self) -> None:
        if not self._pending_batch_review or not self.video_info or not self.character_store:
            return
        source, character_id, character_name, ranges = self._pending_batch_review
        if self.video_info.path.resolve() != source.resolve():
            return
        character = self.character_store.character(character_id)
        if not character:
            QMessageBox.critical(self, "Review unavailable", f"The character {character_name} is no longer available.")
            return
        self.people_page.refresh(character_id)
        self._set_current_target(character, persist=True)
        self.right_panel.set_clips(ranges)
        self._persist_ranges()
        if ranges:
            self._activate_clip(ranges[0])
        self.ready_text.setText(f"Reviewing {len(ranges)} automatic ranges for {character_name}")
        self.statusBar().showMessage(
            "Adjust A/B boundaries and the clip list, then return to Batch and approve reviewed clips.",
            10000,
        )
        self._pending_batch_review = None

    def _batch_review_approved(self, source: Path, character_id: str) -> None:
        if (
            not self.video_info
            or self.video_info.path.resolve() != Path(source).resolve()
            or not self.current_target
            or self.current_target.id != character_id
        ):
            QMessageBox.information(
                self,
                "Open the review video",
                "Open the pending review video and character before approving its clips.",
            )
            return
        self.batch_page.approve_review(self.right_panel.ranges())
        self._select_page(2)

    def _save_current_frame(self) -> None:
        if not self.video_info or not self.controller:
            QMessageBox.information(self, "No video", "Open a video before saving a frame.")
            return
        if self.player.isPlaying():
            self._stop()
        else:
            self._decode_current_position()
        if self.preview.source.isNull():
            QMessageBox.critical(self, "Screenshot failed", "The current video frame could not be decoded.")
            return
        timestamp = format_timecode(self.current_ms)
        filename = choose_png_file(
            self,
            "Save current video frame",
            f"{self.video_info.path.stem}_{date_tag()}--{timestamp}.png",
            key="screenshot_folder",
            preferred_start=self.video_info.path.parent,
        )
        if not filename:
            return
        target = Path(filename)
        if not self.preview.source.save(str(target), "PNG"):
            QMessageBox.critical(self, "Screenshot failed", f"Could not save the PNG to:\n{target}")
            return
        self.ready_text.setText(f"Frame saved · {target.name}")

    def _video_open_failed(self, detail: str) -> None:
        if self.video_info:
            self._set_video_controls_enabled(True)
            self._set_status_for_info(self.video_info)
            self.ready_text.setText("Ready")
        else:
            self.ready_text.setText("Open failed")
            self.status_text.setText("  No video loaded    •   ")
        QMessageBox.critical(self, "Could not open video", detail)

    def _on_player_position(self, milliseconds: int) -> None:
        if self.video_info is None:
            return
        self.current_ms = max(0, min(milliseconds, self.video_info.duration_ms))
        self._updating_timeline = True
        self.timeline.setValue(self.current_ms)
        self._updating_timeline = False
        self._update_timecode()
        self.filters_page.set_position(self.current_ms)
        if (
            self.selection_play_end_ms is not None
            and self.player.isPlaying()
            and self.current_ms >= self.selection_play_end_ms
        ):
            end_ms = self.selection_play_end_ms
            self.selection_play_end_ms = None
            self._seek(end_ms)

    def _on_timeline_changed(self, milliseconds: int) -> None:
        if self._updating_timeline or self.video_info is None:
            return
        self.current_ms = milliseconds
        self._update_timecode()
        if self._timeline_scrubbing:
            self.player.previewPosition(milliseconds)

    def _on_scrub_started(self) -> None:
        self._timeline_scrubbing = True
        self.selection_play_end_ms = None
        self._manual_frame = False
        self.video_stack.setCurrentWidget(self.vlc_surface)
        self.player.beginPreview(self.current_ms)
        if hasattr(self, "batch_page"):
            self.batch_page.set_foreground_busy(True)
        self.ready_text.setText("Scrubbing")

    def _on_scrub_finished(self, milliseconds: int) -> None:
        self.player.endPreview()
        self._timeline_scrubbing = False
        self.video_stack.setCurrentWidget(self.preview)
        self._seek(milliseconds)
        if hasattr(self, "batch_page"):
            self.batch_page.set_foreground_busy(False)

    def _decode_current_position(self) -> None:
        if not self.controller:
            return
        try:
            frame = self.controller.frame_at(self.current_ms)
            self._show_decoded_frame(frame)
            # A requested millisecond can fall between source frames. Commit
            # the actual displayed frame timestamp everywhere so the picture,
            # timeline handle, timecode and VLC resume point never disagree.
            self._set_position(frame.timestamp_ms, update_player=True)
        except DecodeError:
            pass

    def _start_playback(self) -> None:
        if not self.video_info:
            return
        self._manual_frame = False
        self.video_stack.setCurrentWidget(self.vlc_surface)
        self.player.setPosition(self.current_ms)
        self.player.play()
        self.ready_text.setText("Playing")

    def _play(self) -> None:
        self.selection_play_end_ms = None
        self._start_playback()

    def _play_selection(self) -> None:
        if self.marker_a is None or self.marker_b is None or self.marker_b <= self.marker_a:
            QMessageBox.information(self, "Set A and B", "Set a valid A–B range before playing the selection.")
            return
        self._seek(self.marker_a)
        self.selection_play_end_ms = self.marker_b
        self._start_playback()

    def _toggle_playback(self) -> None:
        if self.player.isPlaying():
            self._stop()
        else:
            self._play()

    def _stop(self, *, decode_frame: bool = True) -> None:
        was_playing = self.player.isPlaying()
        self.selection_play_end_ms = None
        self.player.pause()
        if hasattr(self, "video_stack"):
            self.video_stack.setCurrentWidget(self.preview)
        if self.video_info:
            self._manual_frame = True
            self.ready_text.setText("Ready")
            if was_playing and decode_frame:
                self._decode_current_position()

    def _on_playback_state_changed(self, state) -> None:
        if not hasattr(self, "play_button"):
            return
        playing = bool(state)
        self.play_button.setIcon(line_icon("pause" if playing else "play", size=20))
        self.play_button.setToolTip(f"{'Pause' if playing else 'Play'} (Space)")
        if hasattr(self, "batch_page"):
            self.batch_page.set_foreground_busy(playing)

    def _set_playback_volume(self, value: int) -> None:
        self.player.setVolume(value)

    def _toggle_playback_mute(self) -> None:
        self.playback_muted = not self.playback_muted
        self.player.setMuted(self.playback_muted)
        self.volume_button.setIcon(line_icon("muted" if self.playback_muted else "volume", size=19))
        self.volume_button.setToolTip("Unmute preview audio" if self.playback_muted else "Mute preview audio")

    def _seek(self, milliseconds: int) -> None:
        if not self.video_info:
            return
        self._stop(decode_frame=False)
        self.current_ms = max(0, min(milliseconds, self.video_info.duration_ms))
        self._set_position(self.current_ms, update_player=True)
        self._decode_current_position()

    def _jump_to_end(self) -> None:
        if self.video_info:
            self._seek(max(0, self.video_info.duration_ms - 1))

    def _step_frame(self, forward: bool) -> None:
        if not self.controller:
            return
        self._stop(decode_frame=False)
        try:
            frame = self.controller.next_frame(self.current_ms) if forward else self.controller.previous_frame(self.current_ms)
            self._show_decoded_frame(frame)
            self._set_position(frame.timestamp_ms, update_player=True)
        except DecodeError as exc:
            self.ready_text.setText(str(exc))

    def _step_keyframe(self, forward: bool) -> None:
        if not self.video_info or not self.video_info.keyframes_ms:
            return
        frames = self.video_info.keyframes_ms
        if forward:
            index = bisect.bisect_right(frames, self.current_ms + 1)
            target = frames[index] if index < len(frames) else max(0, self.video_info.duration_ms - 1)
        else:
            index = bisect.bisect_left(frames, self.current_ms - 1) - 1
            target = frames[index] if index >= 0 else 0
        self._seek(target)

    def _set_position(self, milliseconds: int, update_player: bool) -> None:
        self.current_ms = milliseconds
        self._updating_timeline = True
        self.timeline.setValue(milliseconds)
        self._updating_timeline = False
        if update_player:
            self.player.setPosition(milliseconds)
        self._update_timecode()
        if hasattr(self, "filters_page"):
            self.filters_page.set_position(milliseconds)

    def _show_decoded_frame(self, frame: DecodedFrame) -> None:
        pixmap = self._pixmap_for_decoded(frame)
        self.preview.set_pixmap(pixmap)
        if (
            hasattr(self, "filters_page")
            and hasattr(self, "filters_dialog")
            and self.filters_dialog.isVisible()
        ):
            self.filters_page.set_source_frame(pixmap, frame.timestamp_ms, refresh=True)

    def _set_marker_a(self) -> None:
        if not self.video_info:
            return
        self.marker_a = self.current_ms
        self.timeline.marker_a = self.marker_a
        self.timeline.update()
        self._update_marker_labels()
        if self.marker_b == self.marker_a:
            self.ready_text.setText(f"Markers A and B set at {format_timecode(self.marker_a)}")
        else:
            self.ready_text.setText(f"Marker A set at {format_timecode(self.marker_a)}")

    def _set_marker_b(self) -> None:
        if not self.video_info:
            return
        self.marker_b = self.current_ms
        self.timeline.marker_b = self.marker_b
        self.timeline.update()
        self._update_marker_labels()
        if self.marker_a == self.marker_b:
            self.ready_text.setText(f"Markers A and B set at {format_timecode(self.marker_b)}")
        else:
            self.ready_text.setText(f"Marker B set at {format_timecode(self.marker_b)}")

    def _add_manual_range(self) -> None:
        self._append_current_range(show_errors=True)

    def _append_current_range(self, show_errors: bool) -> ClipRange | None:
        if self.marker_a is None or self.marker_b is None:
            if show_errors:
                QMessageBox.information(self, "Set A and B", "Set both A and B markers before adding a clip.")
            return None
        if self.marker_b <= self.marker_a:
            if show_errors:
                QMessageBox.warning(self, "Invalid range", "Marker B must be later than marker A.")
            return None
        clip = ClipRange(start_ms=self.marker_a, end_ms=self.marker_b, origin="manual")
        thumbnail = None
        if self.controller:
            try:
                thumbnail = self._pixmap_for_decoded(self.controller.frame_at(self.marker_a))
            except DecodeError:
                thumbnail = self.preview.source.copy() if not self.preview.source.isNull() else None
        self.right_panel.add_clip(clip, thumbnail)
        self.right_panel.clip_model.clips.sort(key=lambda item: item.start_ms)
        self.right_panel.clip_model.layoutChanged.emit()
        self._persist_ranges()
        self.ready_text.setText("Manual clip added")
        return clip

    def _activate_clip(self, clip: ClipRange | None) -> None:
        if clip is None:
            return
        self.marker_a = clip.start_ms
        self.marker_b = clip.end_ms
        self.timeline.marker_a = clip.start_ms
        self.timeline.marker_b = clip.end_ms
        self.timeline.update()
        self._update_marker_labels()
        self._seek(clip.start_ms)

    def _remove_selected_clip(self) -> None:
        if self.right_panel.remove_selected() is not None:
            self._persist_ranges()
            self.ready_text.setText("Clip removed")

    def _persist_ranges(self) -> None:
        if not self.video_info:
            return
        try:
            save_ranges(self.video_info.path, self.right_panel.ranges())
        except OSError as exc:
            self.ready_text.setText(f"Could not save clip list: {exc}")

    def _filters_changed(self, filters: list[VideoFilter], persist: bool = True) -> None:
        active = any(item.enabled for item in filters)
        self.right_panel.set_filters_active(active)
        filter_text = f"Filters ({len(filters)})" if filters else "Filters"
        self.actions["filters_page"].setText(filter_text)
        if hasattr(self, "page_tabs"):
            self.page_tabs.setTabText(1, filter_text)
        if persist and self.video_info:
            try:
                save_filters(self.video_info.path, filters)
            except OSError as exc:
                self.ready_text.setText(f"Could not save filters: {exc}")

    def _target_activated(self, character: CharacterRecord) -> None:
        self._set_current_target(character, persist=True)
        self._select_page(0)
        self.ready_text.setText(f"Target selected: {character.name}")

    def _set_current_target(self, character: CharacterRecord, persist: bool) -> None:
        self.current_target = character
        self.actions["find_character"].setEnabled(bool(self.video_info))
        self.actions["find_all"].setEnabled(bool(self.video_info))
        self.detected_appearances = []
        self._find_is_next = False
        self.timeline.detections = []
        self.timeline.update()
        self._set_find_next_mode(False)
        self.right_panel.set_target(
            character.name,
            character.scope,
            self.people_page.reference_thumbnails(),
        )
        if persist and self.video_info:
            try:
                save_target_selection(self.video_info.path, character.id, character.scope)
            except OSError as exc:
                self.ready_text.setText(f"Could not save character selection: {exc}")

    def _clear_current_target(self) -> None:
        self.current_target = None
        if hasattr(self, "actions"):
            self.actions["find_character"].setEnabled(False)
            self.actions["find_all"].setEnabled(False)
        self.detected_appearances = []
        self._find_is_next = False
        if hasattr(self, "timeline"):
            self.timeline.detections = []
            self.timeline.update()
        if hasattr(self, "right_panel"):
            self.right_panel.clear_target()

    def _set_find_next_mode(self, enabled: bool) -> None:
        if not hasattr(self, "actions"):
            return
        action = self.actions["find_character"]
        if enabled:
            action.setText("Find next appearance")
            action.setToolTip(
                "Find the next distinct appearance after the character has been absent for a confirmed gap"
            )
        else:
            action.setText("Find character")
            action.setToolTip(
                "Scan the complete video and jump to the first matching frame; successful results are cached"
            )

    def _find_character(self) -> None:
        if not self.video_info or not self.current_target or not self.character_store or not self.face_engine:
            QMessageBox.information(
                self,
                "Choose a target",
                "Open a video, choose a character with reference photos, and use it for this video first.",
            )
            return
        try:
            references = list(self.character_store.iter_reference_bytes(self.current_target.id))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Could not load references", str(exc))
            return
        if not references:
            QMessageBox.information(
                self,
                "No reference photos",
                f"Add at least one usable face reference to {self.current_target.name} before scanning.",
            )
            return
        if self.find_worker and self.find_worker.isRunning():
            return
        if self.find_all_worker and self.find_all_worker.isRunning():
            return

        threshold = self.people_page.match_threshold
        self._find_fingerprint = self.character_store.fingerprint(self.current_target.id)
        self._find_is_next = bool(self.detected_appearances)
        previous_match_ms = self.detected_appearances[-1] if self._find_is_next else None
        if not self._find_is_next:
            cached = load_find_result(
                self.video_info.path,
                self.current_target.id,
                self._find_fingerprint,
                threshold,
            )
            if cached is not None:
                self.detected_appearances = [cached]
                self.timeline.detections = [cached]
                self.timeline.update()
                self._seek(cached)
                self._set_find_next_mode(True)
                message = f"{self.current_target.name} found at {format_timecode(cached)} · cached"
                self.right_panel.set_find_result(
                    f"found at {format_timecode(cached)} · cached", find_next=True
                )
                self.ready_text.setText(message)
                return

        self.player.stop()
        self._manual_frame = True
        if not self._find_is_next:
            self.detected_appearances = []
            self.timeline.detections = []
            self.timeline.update()
        self.right_panel.set_find_running(True)
        self.actions["find_character"].setEnabled(False)
        self._find_start_ms = previous_match_ms or 0
        self._find_end_ms = self.video_info.duration_ms
        if self._find_is_next:
            self.ready_text.setText(f"Finding next appearance of {self.current_target.name}…")
        else:
            self.ready_text.setText(f"Finding {self.current_target.name} in complete video…")

        self.find_progress = QProgressDialog(
            f"Scanning for {self.current_target.name}…",
            "Cancel",
            0,
            max(self._find_start_ms + 1, self._find_end_ms),
            self,
        )
        self.find_progress.setWindowTitle("Find character")
        self.find_progress.setWindowModality(Qt.WindowModal)
        self.find_progress.setRange(0, max(1, self._find_end_ms))
        self.find_progress.setMinimumDuration(0)
        self.find_progress.setAutoClose(False)
        self.find_progress.setAutoReset(False)

        if self.search_face_engine is None:
            factory = getattr(self.face_engine, "search_engine", None)
            self.search_face_engine = factory() if callable(factory) else self.face_engine
        self.find_worker = FindCharacterWorker(
            self.video_info.path,
            self._find_start_ms,
            self._find_end_ms,
            self.search_face_engine,
            self.face_engine,
            references,
            threshold,
            previous_match_ms,
            self,
        )
        worker = self.find_worker
        worker.progressed.connect(self._find_progressed)
        worker.found.connect(self._character_found)
        worker.notFound.connect(self._character_not_found)
        worker.failed.connect(self._character_find_failed)
        worker.cancelled.connect(self._character_find_cancelled)
        worker.finished.connect(lambda: self._find_worker_finished(worker))
        self.find_progress.canceled.connect(worker.cancel)
        worker.start()

    def _find_progressed(self, current_ms: int, total_ms: int, phase: str) -> None:
        if not self.find_progress:
            return
        total = max(self._find_start_ms + 1, total_ms)
        self.find_progress.setRange(self._find_start_ms, total)
        self.find_progress.setValue(max(self._find_start_ms, min(current_ms, total)))
        self.find_progress.setLabelText(
            f"{phase} for {self.current_target.name if self.current_target else 'character'}…\n"
            f"{format_timecode(current_ms)} / {format_timecode(total_ms)}"
        )

    def _character_found(self, result: AppearanceMatch) -> None:
        self._finish_find_ui()
        if self._find_is_next:
            if result.timestamp_ms not in self.detected_appearances:
                self.detected_appearances.append(result.timestamp_ms)
        else:
            self.detected_appearances = [result.timestamp_ms]
        self.timeline.detections = list(self.detected_appearances)
        self.timeline.update()
        self._seek(result.timestamp_ms)
        self._set_find_next_mode(True)
        name = self.current_target.name if self.current_target else "Character"
        if not self._find_is_next and self.video_info and self.current_target and self._find_fingerprint:
            try:
                save_find_result(
                    self.video_info.path,
                    self.current_target.id,
                    self._find_fingerprint,
                    self.people_page.match_threshold,
                    result.timestamp_ms,
                )
            except OSError:
                pass
        message = f"{name} found at {format_timecode(result.timestamp_ms)} · confidence {result.score:.2f}"
        self.right_panel.set_find_result(
            f"found at {format_timecode(result.timestamp_ms)}", find_next=True
        )
        self.ready_text.setText(message)
        self.statusBar().showMessage(message, 8000)

    def _character_not_found(self) -> None:
        name = self.current_target.name if self.current_target else "The selected character"
        self._finish_find_ui()
        if self._find_is_next:
            self.right_panel.set_find_result("no later appearance", find_next=True)
            self.ready_text.setText(f"No later appearance of {name} found")
            title = "No later appearance"
            detail = (
                f"Clipivo confirmed that {name} left the current appearance, then checked the rest "
                "of the video but found no later re-entry."
            )
        else:
            self.right_panel.set_find_result("not found")
            self.ready_text.setText(f"{name} not found")
            title = "Character not found"
            detail = (
                f"Clipivo checked the complete video with both fast and high-resolution passes "
                f"but did not find {name}.\n\n"
                "Try more varied references or a lower Match strictness setting."
            )
        QMessageBox.information(
            self,
            title,
            detail,
        )

    def _character_find_failed(self, detail: str) -> None:
        self._finish_find_ui()
        self.ready_text.setText("Character scan failed")
        QMessageBox.critical(self, "Could not scan video", detail)

    def _character_find_cancelled(self) -> None:
        self._finish_find_ui()
        self.ready_text.setText("Character scan cancelled")

    def _finish_find_ui(self) -> None:
        if self.find_progress:
            self.find_progress.close()
            self.find_progress = None
        self.right_panel.set_find_running(False)
        self.actions["find_character"].setEnabled(bool(self.video_info and self.current_target))

    def _find_all(self) -> None:
        if not self.video_info or not self.current_target or not self.character_store or not self.face_engine:
            QMessageBox.information(
                self,
                "Choose a target",
                "Open a video and choose a character with reference photos first.",
            )
            return
        if (self.find_worker and self.find_worker.isRunning()) or (
            self.find_all_worker and self.find_all_worker.isRunning()
        ):
            return
        if self.batch_page.worker and self.batch_page.worker.isRunning():
            QMessageBox.information(
                self,
                "Batch is running",
                "Wait for the Batch queue to finish, or cancel it, before finding all appearances here.",
            )
            return
        try:
            references = list(self.character_store.iter_reference_bytes(self.current_target.id))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Could not load references", str(exc))
            return
        if not references:
            QMessageBox.information(
                self,
                "No reference photos",
                f"Add at least one usable face reference to {self.current_target.name} before scanning.",
            )
            return
        target = BatchCharacterTarget(
            self.current_target.id,
            self.current_target.name,
            references,
            self.people_page.match_threshold,
        )
        self.player.stop()
        self._manual_frame = True
        self.right_panel.set_find_all_running(True)
        self.actions["find_character"].setEnabled(False)
        self.actions["find_all"].setEnabled(False)
        self.ready_text.setText(f"Finding every appearance of {target.name}…")
        self.find_all_progress = QProgressDialog(
            f"Finding every appearance of {target.name}…", "Cancel", 0, max(1, self.video_info.duration_ms), self
        )
        self.find_all_progress.setWindowTitle("Find all")
        self.find_all_progress.setWindowModality(Qt.WindowModal)
        self.find_all_progress.setMinimumDuration(0)
        self.find_all_progress.setAutoClose(False)
        self.find_all_progress.setAutoReset(False)
        if self.find_all_face_engine is None:
            factory = getattr(self.face_engine, "wide_shot_engine", None)
            self.find_all_face_engine = factory() if callable(factory) else self.face_engine
        worker = FindAllWorker(
            self.video_info.path, self.video_info.duration_ms, self.find_all_face_engine, target, self
        )
        self.find_all_worker = worker
        worker.progressed.connect(self._find_all_progressed)
        worker.finishedRanges.connect(self._find_all_finished)
        worker.failed.connect(self._find_all_failed)
        worker.cancelled.connect(self._find_all_cancelled)
        worker.finished.connect(lambda: self._find_all_worker_finished(worker))
        self.find_all_progress.canceled.connect(worker.cancel)
        worker.start()

    def _find_all_progressed(self, current: int, total: int, label: str) -> None:
        if not self.find_all_progress:
            return
        self.find_all_progress.setRange(0, max(1, total))
        self.find_all_progress.setValue(max(0, min(current, total)))
        if label.startswith("Scanning"):
            label = f"{label}…\n{format_timecode(current)} / {format_timecode(total)}"
        self.find_all_progress.setLabelText(label)

    def _finish_find_all_ui(self) -> None:
        if self.find_all_progress:
            self.find_all_progress.close()
            self.find_all_progress = None
        self.right_panel.set_find_all_running(False)
        has_target = bool(self.video_info and self.current_target)
        self.actions["find_character"].setEnabled(has_target)
        self.actions["find_all"].setEnabled(has_target)

    def _find_all_finished(self, ranges: list[ClipRange]) -> None:
        self._finish_find_all_ui()
        name = self.current_target.name if self.current_target else "The selected character"
        if not ranges:
            self.right_panel.set_find_result("not found")
            self.ready_text.setText(f"{name} not found")
            QMessageBox.information(
                self,
                "Character not found",
                f"Clipivo scanned the complete video but did not find {name}.\n\n"
                "Try more varied references or a lower Match strictness setting.",
            )
            return
        # Earlier automatic results are replaced; clips you marked by hand stay.
        manual = [item for item in self.right_panel.ranges() if not item.origin.startswith("automatic")]
        clips = sorted([*manual, *ranges], key=lambda item: item.start_ms)
        self.right_panel.set_clips(clips)
        self.right_panel.select_stitched_output()
        self._persist_ranges()
        # Mark every appearance on the timeline without arming Find next.
        self.detected_appearances = []
        self._set_find_next_mode(False)
        self.right_panel.set_find_next_available(False)
        self.timeline.detections = [item.start_ms for item in ranges]
        self.timeline.update()
        self._activate_clip(ranges[0])
        total_ms = sum(item.duration_ms for item in ranges)
        summary = (
            f"{len(ranges)} appearance{'s' if len(ranges) != 1 else ''} · "
            f"{format_timecode(total_ms)} of {format_timecode(self.video_info.duration_ms)}"
        )
        self.right_panel.set_find_result(summary)
        self.ready_text.setText(f"{name}: {summary}")
        box = QMessageBox(self)
        box.setWindowTitle("Clips ready")
        box.setIcon(QMessageBox.Information)
        box.setText(f"Found {name} {len(ranges)} time{'s' if len(ranges) != 1 else ''}.")
        box.setInformativeText(
            f"Together that is {format_timecode(total_ms)} of video. "
            "Save them now as one stitched video, or review the clips on the right first."
        )
        save = box.addButton("Save stitched video", QMessageBox.AcceptRole)
        box.addButton("Review first", QMessageBox.RejectRole)
        box.setDefaultButton(save)
        box.exec()
        if box.clickedButton() is save:
            self._request_export_selected_mode()

    def _find_all_failed(self, detail: str) -> None:
        self._finish_find_all_ui()
        self.ready_text.setText("Character scan failed")
        QMessageBox.critical(self, "Could not scan video", detail)

    def _find_all_cancelled(self) -> None:
        self._finish_find_all_ui()
        self.ready_text.setText("Character scan cancelled")

    def _find_all_worker_finished(self, worker: FindAllWorker) -> None:
        if self.find_all_worker is worker:
            self.find_all_worker = None
        worker.deleteLater()

    def _find_worker_finished(self, worker: FindCharacterWorker) -> None:
        if self.find_worker is worker:
            self.find_worker = None
        worker.deleteLater()

    def _target_references_changed(self, character: CharacterRecord) -> None:
        if self.current_target and self.current_target.id == character.id:
            self._set_current_target(character, persist=False)

    def _target_character_deleted(self, character_id: str) -> None:
        if not self.current_target or self.current_target.id != character_id:
            return
        self._clear_current_target()
        if self.video_info:
            try:
                save_target_selection(self.video_info.path, "", "")
            except OSError as exc:
                self.ready_text.setText(f"Could not clear character selection: {exc}")

    def _face_settings_changed(self, use_gpu: bool, detector_size: int, threshold: float) -> None:
        self.people_page.set_match_threshold(threshold)
        if hasattr(self, "batch_page"):
            self.batch_page.set_match_threshold(threshold)
        self.search_face_engine = None
        self.find_all_face_engine = None
        if self.face_engine:
            if hasattr(self.face_engine, "use_gpu"):
                self.face_engine.use_gpu = use_gpu
            if hasattr(self.face_engine, "detector_size"):
                self.face_engine.detector_size = detector_size
            if hasattr(self.face_engine, "_application"):
                self.face_engine._application = None
        self.people_page._update_engine_status()

    def _export_settings_changed(self, video_encoder: str) -> None:
        self.export_video_encoder = video_encoder if video_encoder in {"auto", "nvenc", "cpu"} else "auto"

    def _request_export(self, mode: str, stitched: bool = False, include_audio: bool = True) -> None:
        if not self.video_info:
            return
        clips = self._enabled_clips_for_export()
        if not clips:
            if self.right_panel.ranges():
                message = "Enable at least one clip in the clip list before exporting."
            else:
                message = "Set a valid A and B selection before exporting."
            QMessageBox.information(self, "No clips", message)
            return
        if mode == "copy" and any(not self._is_keyframe(clip.start_ms) for clip in clips):
            answer = QMessageBox.warning(
                self,
                "Copy boundaries are not exact",
                "One or more clip starts are not on source keyframes. Source copy preserves quality, but the visible start can shift to a nearby keyframe. Use Exact cut / MP4 for exact A/B boundaries.\n\nContinue with source copy?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        target: Path | None = None
        if stitched or len(clips) == 1:
            filename = choose_save_file(
                self,
                "Save exported video",
                f"{self.video_info.path.stem}_{date_tag()}.mp4",
                key="export_folder",
                preferred_start=self.video_info.path.parent,
            )
            if not filename:
                return
            target = Path(filename)
            output_dir = target.parent
        else:
            output = choose_directory(
                self,
                "Choose export folder",
                key="export_folder",
                preferred_start=self.video_info.path.parent,
            )
            if not output:
                return
            output_dir = Path(output)
        self._start_export(output_dir, mode, stitched, include_audio, target)

    def _request_export_selected_mode(self) -> None:
        if not hasattr(self, "right_panel"):
            self._request_export("copy", False, True)
            return
        self._request_export(
            str(self.right_panel.output.currentData()),
            self.right_panel.arrangement.currentData() == "stitched",
            self.right_panel.include_audio.isChecked(),
        )

    def _enabled_clips_for_export(self) -> list[ClipRange]:
        clips = [item for item in self.right_panel.ranges() if item.enabled]
        if not clips and not self.right_panel.ranges():
            clip = self._append_current_range(show_errors=False)
            if clip is not None:
                clips = [clip]
        return clips

    def _start_export(
        self,
        output_dir: Path,
        mode: str,
        stitched: bool,
        include_audio: bool,
        target: Path | None = None,
    ) -> None:
        self.export_worker = ExportWorker(
            self.video_info.path,
            self.right_panel.ranges(),
            output_dir,
            mode,
            stitched,
            include_audio,
            target,
            self.filters_page.filters(),
            self.video_info.fps,
            self.export_video_encoder,
            self,
        )
        self.export_progress = QProgressDialog("Preparing export…", "Cancel", 0, 1, self)
        self.export_progress.setWindowTitle("Exporting clips")
        self.export_progress.setWindowModality(Qt.WindowModal)
        self.export_progress.setMinimumDuration(0)
        self.export_progress.canceled.connect(self.export_worker.cancel)
        self.export_worker.progressed.connect(self._export_progressed)
        self.export_worker.succeeded.connect(self._export_succeeded)
        self.export_worker.failed.connect(self._export_failed)
        self.export_worker.start()

    def _export_progressed(self, done: int, total: int, text: str) -> None:
        if self.export_progress:
            self.export_progress.setMaximum(max(1, total))
            self.export_progress.setValue(done)
            self.export_progress.setLabelText(text)

    def _export_succeeded(self, outputs: list[Path]) -> None:
        if self.export_progress:
            self.export_progress.close()
        self.ready_text.setText("Export complete")
        QMessageBox.information(self, "Export complete", f"Created {len(outputs)} file(s) in:\n{outputs[0].parent}")

    def _export_failed(self, detail: str) -> None:
        if self.export_progress:
            self.export_progress.close()
        self.ready_text.setText("Export failed")
        QMessageBox.critical(self, "Export failed", detail + "\n\nSee clipivo-export.log in the selected output folder.")

    def _is_keyframe(self, milliseconds: int) -> bool:
        return bool(self.video_info and any(abs(value - milliseconds) <= 2 for value in self.video_info.keyframes_ms))

    def _set_status_for_info(self, info: VideoInfo) -> None:
        audio_count = sum(stream.kind == "audio" for stream in info.streams)
        self.status_text.setText(
            f"  {info.width}×{info.height}    •    {info.fps:.3f} fps    •    {info.video_codec.upper()}    •    {audio_count} audio    •   "
        )

    @staticmethod
    def _pixmap_for_decoded(frame: DecodedFrame) -> QPixmap:
        image = QImage(frame.rgb, frame.width, frame.height, frame.line_size, QImage.Format_RGB888).copy()
        return QPixmap.fromImage(image)

    def _update_timecode(self) -> None:
        duration = self.video_info.duration_ms if self.video_info else 0
        self.timecode_label.setText(f"{format_timecode(self.current_ms)}  /  {format_timecode(duration)}")

    def _update_marker_labels(self) -> None:
        self.marker_a_label.setText(f"A:  {format_timecode(self.marker_a)}")
        self.marker_b_label.setText(f"B:  {format_timecode(self.marker_b)}")
        if self.marker_a is not None and self.marker_a == self.marker_b:
            self.timeline.setAccessibleDescription(
                f"Markers A and B overlap at {format_timecode(self.marker_a)}"
            )
        else:
            self.timeline.setAccessibleDescription(
                f"Marker A {format_timecode(self.marker_a)}; marker B {format_timecode(self.marker_b)}"
            )
        if hasattr(self, "filters_page"):
            self.filters_page.set_markers(self.marker_a, self.marker_b)

    def _set_video_controls_enabled(self, enabled: bool) -> None:
        for key in (
            "export", "screenshot", "marker_a", "marker_b", "add_range", "remove_clip", "start", "end",
            "previous_frame", "next_frame", "previous_keyframe", "next_keyframe", "play", "play_selection", "stop",
            "filters_page",
        ):
            self.actions[key].setEnabled(enabled)
        if hasattr(self, "transport_buttons"):
            for button in (*self.transport_buttons, *self.marker_buttons):
                button.setEnabled(enabled)
        if hasattr(self, "right_panel"):
            self.right_panel.save_button.setEnabled(enabled)
            self.right_panel.add_range_button.setEnabled(enabled)
        if hasattr(self, "page_tabs"):
            self.page_tabs.setTabEnabled(1, enabled)

    def _collapse_right(self) -> None:
        total = max(1, sum(self.video_splitter.sizes()))
        self.right_panel.hide()
        self.restore_pane_button.show()
        self.side_container.setMinimumWidth(24)
        self.side_container.setMaximumWidth(24)
        self.video_splitter.setSizes([max(1, total - 24), 24])

    def _restore_right(self) -> None:
        total = max(1, sum(self.video_splitter.sizes()))
        self.restore_pane_button.hide()
        self.side_container.setMinimumWidth(264)
        self.side_container.setMaximumWidth(310)
        self.right_panel.show()
        target = min(272, max(264, total - 640))
        self.video_splitter.setSizes([max(1, total - target), target])

    def _on_player_error(self, error_string: str) -> None:
        if error_string:
            self.ready_text.setText("Playback error")
            self.statusBar().showMessage(error_string, 8000)

    def _show_about(self) -> None:
        AboutDialog(self).exec()

    def closeEvent(self, event) -> None:
        self.player.shutdown()
        if hasattr(self, "filters_dialog"):
            self.filters_dialog.close()
        if hasattr(self, "filters_page"):
            self.filters_page.close_preview_process()
        if hasattr(self, "people_page"):
            self.people_page.shutdown()
        if hasattr(self, "batch_page") and self.batch_page.worker and self.batch_page.worker.isRunning():
            self.batch_page.worker.cancel()
            self.batch_page.worker.wait(5000)
        if self.controller:
            self.controller.close()
        if self.export_worker and self.export_worker.isRunning():
            self.export_worker.cancel()
            self.export_worker.wait(3000)
        if self.find_worker and self.find_worker.isRunning():
            self.find_worker.cancel()
            self.find_worker.wait(5000)
        if self.find_all_worker and self.find_all_worker.isRunning():
            self.find_all_worker.cancel()
            self.find_all_worker.wait(5000)
        if self.probe_worker and self.probe_worker.isRunning():
            self.probe_worker.wait(5000)
        super().closeEvent(event)
