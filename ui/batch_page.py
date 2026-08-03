from __future__ import annotations

from pathlib import Path
from threading import Event

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.batch import (
    BatchCharacterTarget,
    BatchConfiguration,
    BatchVideoEntry,
    CharacterAppearances,
    analyse_video_for_characters,
    discover_videos,
    load_batch_configuration,
    move_source_to_trash,
    save_batch_configuration,
    validate_export,
)
from core.character_store import CharacterStore
from core.export import ExportError, export_ranges
from core.face_engine import FaceEngine
from core.models import ClipRange
from core.video_probe import probe_video

from .native_dialogs import choose_directory, choose_videos
from .styles import PALETTE


class BatchExecutionWorker(QThread):
    entryUpdated = Signal(str, str, int, str, object)
    reviewRequired = Signal(str, str, str, str, object)
    allFinished = Signal(bool)

    def __init__(
        self,
        configuration: BatchConfiguration,
        targets: list[BatchCharacterTarget],
        face_engine: FaceEngine,
        parent=None,
    ):
        super().__init__(parent)
        self.configuration = BatchConfiguration.from_dict(configuration.to_dict())
        self.targets = targets
        self.face_engine = face_engine
        self._pause_event = Event()
        self._pause_event.set()
        self._cancel_event = Event()
        self._review_event = Event()
        self._review_ranges: list[ClipRange] | None = None

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._pause_event.clear()
        else:
            self._pause_event.set()

    def cancel(self) -> None:
        self._cancel_event.set()
        self.requestInterruption()
        self._pause_event.set()
        self._review_event.set()

    def approve_review(self, ranges: list[ClipRange]) -> None:
        self._review_ranges = [ClipRange.from_dict(item.to_dict()) for item in ranges if item.enabled]
        self._review_event.set()

    def run(self) -> None:
        cancelled = False
        for entry in self.configuration.videos:
            if not entry.enabled:
                continue
            if self._cancelled():
                cancelled = True
                break
            try:
                self._process_entry(entry)
            except ExportError as exc:
                if self._cancelled():
                    self.entryUpdated.emit(entry.id, "cancelled", entry.progress, "Cancelled", [])
                    cancelled = True
                    break
                self.entryUpdated.emit(entry.id, "failed", entry.progress, str(exc), [])
            except Exception as exc:  # noqa: BLE001
                if self._cancelled():
                    self.entryUpdated.emit(entry.id, "cancelled", entry.progress, "Cancelled", [])
                    cancelled = True
                    break
                self.entryUpdated.emit(entry.id, "failed", entry.progress, str(exc), [])
        self.allFinished.emit(cancelled or self._cancelled())

    def _process_entry(self, entry: BatchVideoEntry) -> None:
        source = entry.path
        if not source.is_file():
            raise RuntimeError(f"Source file is missing: {source}")
        self.entryUpdated.emit(entry.id, "analysing", 1, "Reading video", [])
        info = probe_video(source, include_keyframes=False)
        results = analyse_video_for_characters(
            source,
            self.face_engine,
            self.targets,
            duration_ms=info.duration_ms,
            quality=self.configuration.quality,
            cancelled=self._cancelled,
            wait_if_paused=self._wait_if_paused,
            progress=lambda current, total, label: self.entryUpdated.emit(
                entry.id,
                "analysing",
                min(70, round(current / max(1, total) * 70)),
                label,
                [],
            ),
        )
        if self._cancelled():
            raise ExportError("Batch cancelled.")

        outputs: list[Path] = []
        missing_characters: list[str] = []
        exportable = [result for result in results if result.ranges]
        missing_characters.extend(result.character_name for result in results if not result.ranges)
        if not exportable:
            self.entryUpdated.emit(
                entry.id, "no_match", 100,
                "No selected character was found · source kept", [],
            )
            return

        for character_index, result in enumerate(exportable):
            ranges = list(result.ranges)
            if entry.manual_review:
                ranges = self._request_review(entry, result, ranges)
                if not ranges:
                    missing_characters.append(result.character_name)
                    continue
            if self._cancelled():
                raise ExportError("Batch cancelled.")
            output_dir = Path(self.configuration.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            base_progress = 70 + round(character_index / max(1, len(exportable)) * 25)
            span = max(1, round(25 / max(1, len(exportable))))
            character_outputs = export_ranges(
                source,
                ranges,
                output_dir,
                "accurate",
                self.configuration.arrangement == "compilation",
                include_audio=self.configuration.include_audio,
                progress=lambda done, total, text, base=base_progress, width=span: self.entryUpdated.emit(
                    entry.id,
                    "exporting",
                    min(95, base + round(done / max(1, total) * width)),
                    f"{result.character_name} · {text}",
                    [str(path) for path in outputs],
                ),
                cancel_event=self._cancel_event,
                source_fps=info.fps,
                video_encoder=self.configuration.video_encoder,
                output_character=result.character_name,
                source_video_bitrate_kbps=getattr(info, "video_bitrate_kbps", 0),
            )
            if self.configuration.arrangement == "compilation":
                expected_durations = [sum(item.duration_ms for item in ranges)]
            else:
                expected_durations = [item.duration_ms for item in ranges]
            for output, expected_duration in zip(character_outputs, expected_durations, strict=True):
                try:
                    validate_export(output, expected_duration_ms=expected_duration)
                except Exception:
                    output.unlink(missing_ok=True)
                    raise
            outputs.extend(character_outputs)

        if not outputs:
            self.entryUpdated.emit(entry.id, "no_match", 100, "No reviewed ranges to export · source kept", [])
            return

        detail = f"Completed · {len(outputs)} output{'s' if len(outputs) != 1 else ''}"
        all_requested_succeeded = not missing_characters
        if missing_characters:
            detail += f" · no match: {', '.join(missing_characters)} · source kept"
        if self.configuration.trash_after_success and all_requested_succeeded:
            self.entryUpdated.emit(entry.id, "exporting", 98, "Outputs verified · moving source to Trash", [str(path) for path in outputs])
            move_source_to_trash(source)
            detail += " · source moved to Trash"
        self.entryUpdated.emit(entry.id, "completed", 100, detail, [str(path) for path in outputs])

    def _request_review(
        self,
        entry: BatchVideoEntry,
        result: CharacterAppearances,
        ranges: list[ClipRange],
    ) -> list[ClipRange]:
        self._review_ranges = None
        self._review_event.clear()
        self.reviewRequired.emit(
            entry.id,
            entry.source,
            result.character_id,
            result.character_name,
            [ClipRange.from_dict(item.to_dict()) for item in ranges],
        )
        while not self._review_event.wait(0.1):
            if self._cancelled():
                return []
        return self._review_ranges or []

    def _wait_if_paused(self) -> bool:
        while not self._pause_event.wait(0.1):
            if self._cancelled():
                return False
        return not self._cancelled()

    def _cancelled(self) -> bool:
        return self._cancel_event.is_set() or self.isInterruptionRequested()


class BatchPage(QWidget):
    openVideoRequested = Signal(object)
    reviewRequested = Signal(object, str, str, object)
    approveReviewRequested = Signal(object, str)
    runningChanged = Signal(bool)

    def __init__(
        self,
        character_store: CharacterStore | None,
        face_engine: FaceEngine | None,
        parent=None,
    ):
        super().__init__(parent)
        self.character_store = character_store
        self.face_engine = face_engine
        self.configuration = load_batch_configuration()
        self.worker: BatchExecutionWorker | None = None
        self.match_threshold = 0.40
        self._paused = False
        self._foreground_busy = False
        self._review_entry_id = ""
        self._review_source: Path | None = None
        self._review_character_id = ""
        self._building = True
        self._build_ui()
        self._populate_queue()
        self.refresh_characters()
        self._load_configuration_controls()
        self._building = False
        self._update_actions()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(5)

        heading = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel("Batch")
        title.setObjectName("pageTitle")
        title.setToolTip("Process one or many videos in the background")
        subtitle = QLabel("Queue videos, select characters, review when needed, and export every appearance.")
        subtitle.setObjectName("pageSubtitle")
        subtitle.setToolTip("Each source is decoded once for all selected reusable characters")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        heading.addLayout(title_box)
        heading.addStretch()
        self.summary = QLabel("0 videos · 0 selected characters")
        self.summary.setObjectName("batchSummary")
        self.summary.setToolTip("Current enabled batch size")
        heading.addWidget(self.summary)
        root.addLayout(heading)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(1)
        root.addWidget(splitter, 1)

        queue_panel = QWidget()
        queue_layout = QVBoxLayout(queue_panel)
        queue_layout.setContentsMargins(0, 0, 5, 0)
        queue_layout.setSpacing(4)
        tools = QHBoxLayout()
        self.add_videos_button = QPushButton("Add videos")
        self.add_videos_button.setToolTip("Add one or more supported video files to the queue")
        self.add_videos_button.clicked.connect(self._choose_videos)
        self.add_folder_button = QPushButton("Add folder")
        self.add_folder_button.setToolTip("Recursively add supported videos from a folder")
        self.add_folder_button.clicked.connect(self._choose_folder)
        self.remove_button = QPushButton("Remove")
        self.remove_button.setToolTip("Remove selected queue rows without touching source files")
        self.remove_button.clicked.connect(self._remove_selected)
        self.open_editor_button = QPushButton("Open in editor")
        self.open_editor_button.setToolTip("Open the selected source in the Video tab for manual work")
        self.open_editor_button.clicked.connect(self._open_selected)
        for button in (self.add_videos_button, self.add_folder_button, self.remove_button, self.open_editor_button):
            tools.addWidget(button)
        tools.addStretch()
        queue_layout.addLayout(tools)

        self.queue = QTableWidget(0, 5)
        self.queue.setObjectName("batchQueue")
        self.queue.setHorizontalHeaderLabels(("Run", "Video", "Review", "Status", "Progress"))
        self.queue.verticalHeader().hide()
        self.queue.setSelectionBehavior(QTableWidget.SelectRows)
        self.queue.setSelectionMode(QTableWidget.ExtendedSelection)
        self.queue.setAlternatingRowColors(True)
        self.queue.setToolTip("Double-click a video to open it in the manual editor")
        self.queue.horizontalHeader().setStretchLastSection(False)
        self.queue.horizontalHeader().resizeSection(0, 42)
        self.queue.horizontalHeader().resizeSection(2, 62)
        self.queue.horizontalHeader().resizeSection(3, 260)
        self.queue.horizontalHeader().resizeSection(4, 120)
        self.queue.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.queue.itemChanged.connect(self._queue_item_changed)
        self.queue.itemDoubleClicked.connect(lambda _item: self._open_selected())
        self.queue.itemSelectionChanged.connect(self._update_actions)
        queue_layout.addWidget(self.queue, 1)
        splitter.addWidget(queue_panel)

        config_panel = QWidget()
        config_panel.setObjectName("batchInspector")
        config_panel.setMinimumWidth(320)
        config_panel.setMaximumWidth(370)
        config_layout = QVBoxLayout(config_panel)
        config_layout.setContentsMargins(8, 0, 0, 0)
        config_layout.setSpacing(5)

        characters = QGroupBox("Characters")
        characters.setToolTip("Select one or more reusable characters for every enabled video")
        characters_layout = QVBoxLayout(characters)
        characters_layout.setContentsMargins(7, 6, 7, 7)
        self.characters = QListWidget()
        self.characters.setObjectName("batchCharacters")
        self.characters.setMinimumHeight(130)
        self.characters.setToolTip("Checked characters are matched in one shared video decode pass")
        self.characters.itemChanged.connect(self._configuration_changed)
        characters_layout.addWidget(self.characters)
        config_layout.addWidget(characters, 1)

        output = QGroupBox("Output")
        output.setToolTip("Configure generated video files")
        form = QFormLayout(output)
        form.setContentsMargins(7, 7, 7, 7)
        form.setSpacing(5)
        folder_row = QWidget()
        folder_layout = QHBoxLayout(folder_row)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder_layout.setSpacing(4)
        self.output_folder = QLineEdit()
        self.output_folder.setReadOnly(True)
        self.output_folder.setPlaceholderText("Choose an output folder")
        self.output_folder.setToolTip("All generated clips are written here")
        browse = QPushButton("Browse")
        browse.setToolTip("Choose the batch output folder")
        browse.clicked.connect(self._choose_output_folder)
        folder_layout.addWidget(self.output_folder, 1)
        folder_layout.addWidget(browse)
        form.addRow("Folder", folder_row)

        self.arrangement = QComboBox()
        self.arrangement.addItem("One compilation per video + character", "compilation")
        self.arrangement.addItem("Separate appearance clips", "separate")
        self.arrangement.setToolTip("Choose one stitched reel or individual clips for each character")
        form.addRow("Save as", self.arrangement)
        self.quality = QComboBox()
        self.quality.addItem("Balanced · reference frames / 2 s", "balanced")
        self.quality.addItem("Rapid · keyframes only", "rapid")
        self.quality.addItem("Thorough · sampled / 0.75 s", "thorough")
        self.quality.setToolTip("Balanced is the default; Rapid is faster but can miss very short appearances")
        form.addRow("Analysis", self.quality)
        self.encoder = QComboBox()
        self.encoder.addItem("Auto · NVIDIA when available", "auto")
        self.encoder.addItem("NVIDIA NVENC", "nvenc")
        self.encoder.addItem("CPU · libx264", "cpu")
        self.encoder.setToolTip("Encoder used for exact automatic appearance exports")
        form.addRow("Encoder", self.encoder)
        self.include_audio = QCheckBox("Include audio")
        self.include_audio.setToolTip("Preserve synchronized audio in generated outputs")
        form.addRow("", self.include_audio)
        config_layout.addWidget(output)

        safety = QGroupBox("After success")
        safety.setToolTip("Optional source cleanup after every requested output validates")
        safety_layout = QVBoxLayout(safety)
        safety_layout.setContentsMargins(7, 6, 7, 7)
        self.trash_source = QCheckBox("Move source to Trash")
        self.trash_source.setToolTip(
            "Off by default; only moves a source to Linux Trash after every requested character output succeeds and validates"
        )
        safety_layout.addWidget(self.trash_source)
        safety_hint = QLabel("Never runs after no-match, failure, cancellation, or pending review.")
        safety_hint.setObjectName("targetHint")
        safety_hint.setWordWrap(True)
        safety_hint.setToolTip("Clipivo never permanently deletes source videos")
        safety_layout.addWidget(safety_hint)
        config_layout.addWidget(safety)

        self.review_banner = QLabel("Manual review is waiting. Adjust A/B clips in Video, then approve here.")
        self.review_banner.setObjectName("warningBanner")
        self.review_banner.setWordWrap(True)
        self.review_banner.setToolTip("The background queue is paused until reviewed ranges are approved")
        self.review_banner.hide()
        config_layout.addWidget(self.review_banner)
        self.approve_button = QPushButton("Approve reviewed clips")
        self.approve_button.setObjectName("targetAction")
        self.approve_button.setToolTip("Continue batch export with the currently reviewed A/B clip list")
        self.approve_button.clicked.connect(self._approve_review)
        self.approve_button.hide()
        config_layout.addWidget(self.approve_button)

        run_row = QHBoxLayout()
        self.start_button = QPushButton("Start batch")
        self.start_button.setObjectName("primary")
        self.start_button.setToolTip("Analyse and export all enabled queue entries")
        self.start_button.clicked.connect(self._start)
        self.pause_button = QPushButton("Pause")
        self.pause_button.setToolTip("Pause face analysis between decoded samples")
        self.pause_button.clicked.connect(self._toggle_pause)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setToolTip("Cancel analysis/export safely; source videos remain untouched")
        self.cancel_button.clicked.connect(self._cancel)
        run_row.addWidget(self.start_button, 1)
        run_row.addWidget(self.pause_button)
        run_row.addWidget(self.cancel_button)
        config_layout.addLayout(run_row)
        splitter.addWidget(config_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([900, 340])

        for widget in (self.arrangement, self.quality, self.encoder, self.include_audio, self.trash_source):
            if isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self._configuration_changed)
            else:
                widget.toggled.connect(self._configuration_changed)

    def refresh_characters(self) -> None:
        selected = set(self._selected_character_ids()) if hasattr(self, "characters") else set(self.configuration.character_ids)
        self.characters.blockSignals(True)
        self.characters.clear()
        if self.character_store:
            for character in self.character_store.characters():
                item = QListWidgetItem(character.name)
                item.setData(Qt.UserRole, character.id)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if character.id in selected else Qt.Unchecked)
                references = len(self.character_store.references(character.id))
                item.setToolTip(f"{character.name} · {references} encrypted reference photo{'s' if references != 1 else ''}")
                self.characters.addItem(item)
        self.characters.blockSignals(False)
        self._update_summary()

    def set_match_threshold(self, threshold: float) -> None:
        self.match_threshold = max(0.0, min(1.0, float(threshold)))

    def add_paths(self, paths: list[Path]) -> None:
        known = {str(Path(entry.source).resolve()) for entry in self.configuration.videos}
        changed = False
        for path in paths:
            path = Path(path)
            if not path.is_file():
                continue
            resolved = str(path.resolve())
            if resolved in known:
                continue
            self.configuration.videos.append(BatchVideoEntry(resolved))
            known.add(resolved)
            changed = True
        if changed:
            self._populate_queue()
            self._persist()

    def approve_review(self, ranges: list[ClipRange]) -> None:
        if self.worker and self._review_entry_id:
            self.worker.approve_review(ranges)
            self.review_banner.hide()
            self.approve_button.hide()
            self._review_entry_id = ""
            self._review_source = None
            self._review_character_id = ""

    def set_foreground_busy(self, busy: bool) -> None:
        self._foreground_busy = bool(busy)
        if self.worker and self.worker.isRunning() and not self._review_entry_id:
            self.worker.set_paused(self._paused or self._foreground_busy)
            if self._foreground_busy:
                self.pause_button.setText("Paused for editor")
            else:
                self.pause_button.setText("Resume" if self._paused else "Pause")

    def _choose_videos(self) -> None:
        self.add_paths([Path(path) for path in choose_videos(self)])

    def _choose_folder(self) -> None:
        folder = choose_directory(self, "Add videos from folder", key="batch_source_folder")
        if folder:
            self.add_paths(discover_videos(Path(folder)))

    def _choose_output_folder(self) -> None:
        folder = choose_directory(self, "Choose batch output folder", key="batch_output_folder")
        if folder:
            self.output_folder.setText(folder)
            self._configuration_changed()

    def _remove_selected(self) -> None:
        selected_ids = {
            self.queue.item(index.row(), 1).data(Qt.UserRole)
            for index in self.queue.selectionModel().selectedRows()
        }
        if not selected_ids:
            return
        self.configuration.videos = [entry for entry in self.configuration.videos if entry.id not in selected_ids]
        self._populate_queue()
        self._persist()

    def _open_selected(self) -> None:
        row = self.queue.currentRow()
        if row < 0:
            return
        entry = self._entry_for_row(row)
        if entry:
            self.openVideoRequested.emit(entry.path)

    def _start(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        self._sync_configuration()
        entries = [entry for entry in self.configuration.videos if entry.enabled]
        selected_ids = self._selected_character_ids()
        if not entries:
            QMessageBox.information(self, "Empty batch", "Add and enable at least one source video.")
            return
        if not selected_ids:
            QMessageBox.information(self, "No characters", "Select at least one reusable character.")
            return
        if not self.output_folder.text():
            self._choose_output_folder()
            if not self.output_folder.text():
                return
            self._sync_configuration()
        if not self.character_store or not self.face_engine:
            QMessageBox.critical(self, "Batch unavailable", "The local character store or face engine is unavailable.")
            return
        targets: list[BatchCharacterTarget] = []
        for character_id in selected_ids:
            character = self.character_store.character(character_id)
            references = list(self.character_store.iter_reference_bytes(character_id))
            if not character or not references:
                QMessageBox.information(self, "Missing references", "Every selected character needs at least one reference photo.")
                return
            targets.append(BatchCharacterTarget(
                character.id,
                character.name,
                references,
                self.match_threshold,
            ))
        if self.configuration.trash_after_success:
            answer = QMessageBox.warning(
                self,
                "Move successful sources to Trash?",
                "After every requested character output succeeds and validates, Clipivo will move that source video to the Linux Trash. It will never permanently delete it.\n\nContinue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        for entry in entries:
            entry.status = "queued"
            entry.progress = 0
            entry.detail = "Queued"
            entry.outputs = []
        self._populate_queue()
        self.worker = BatchExecutionWorker(self.configuration, targets, self.face_engine, self)
        worker = self.worker
        worker.entryUpdated.connect(self._entry_updated)
        worker.reviewRequired.connect(self._review_required)
        worker.allFinished.connect(self._batch_finished)
        worker.finished.connect(self._worker_stopped)
        worker.finished.connect(worker.deleteLater)
        self._paused = False
        self.pause_button.setText("Pause")
        self.runningChanged.emit(True)
        self._update_actions()
        worker.start()

    def _toggle_pause(self) -> None:
        if not self.worker or not self.worker.isRunning():
            return
        self._paused = not self._paused
        self.worker.set_paused(self._paused or self._foreground_busy)
        self.pause_button.setText("Resume" if self._paused else ("Paused for editor" if self._foreground_busy else "Pause"))

    def _cancel(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.cancel()

    def _review_required(
        self,
        entry_id: str,
        source: str,
        character_id: str,
        character_name: str,
        ranges: list[ClipRange],
    ) -> None:
        self._review_entry_id = entry_id
        self._review_source = Path(source)
        self._review_character_id = character_id
        self.review_banner.setText(
            f"Review {len(ranges)} appearance range{'s' if len(ranges) != 1 else ''} for {character_name} in Video, then approve here."
        )
        self.review_banner.show()
        self.approve_button.show()
        self._entry_updated(entry_id, "review", 70, f"Waiting for manual review · {character_name}", [])
        self.reviewRequested.emit(Path(source), character_id, character_name, ranges)

    def _approve_review(self) -> None:
        if self._review_source and self._review_character_id:
            self.approveReviewRequested.emit(self._review_source, self._review_character_id)

    def _entry_updated(self, entry_id: str, status: str, progress: int, detail: str, outputs: list[str]) -> None:
        entry = next((item for item in self.configuration.videos if item.id == entry_id), None)
        if not entry:
            return
        entry.status = status
        entry.progress = progress
        entry.detail = detail
        if outputs:
            entry.outputs = list(outputs)
        row = self.configuration.videos.index(entry)
        status_item = self.queue.item(row, 3)
        status_item.setText(detail)
        status_item.setToolTip(detail)
        bar = self.queue.cellWidget(row, 4)
        if isinstance(bar, QProgressBar):
            bar.setValue(progress)
            bar.setFormat(f"{progress}%")
        self._persist()

    def _batch_finished(self, cancelled: bool) -> None:
        self.runningChanged.emit(False)
        self._paused = False
        self.pause_button.setText("Pause")
        if cancelled:
            for entry in self.configuration.videos:
                if entry.status in {"queued", "analysing", "exporting", "review"}:
                    entry.status = "cancelled"
                    entry.detail = "Cancelled · source kept"
            self._populate_queue()
        self._persist()
        self._update_actions()

    def _worker_stopped(self) -> None:
        self.worker = None
        self._update_actions()

    def _queue_item_changed(self, item: QTableWidgetItem) -> None:
        if self._building or item.column() not in {0, 2}:
            return
        entry = self._entry_for_row(item.row())
        if not entry:
            return
        if item.column() == 0:
            entry.enabled = item.checkState() == Qt.Checked
        else:
            entry.manual_review = item.checkState() == Qt.Checked
        self._persist()
        self._update_summary()

    def _populate_queue(self) -> None:
        was_building = self._building
        self._building = True
        self.queue.setRowCount(len(self.configuration.videos))
        for row, entry in enumerate(self.configuration.videos):
            run_item = QTableWidgetItem()
            run_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            run_item.setCheckState(Qt.Checked if entry.enabled else Qt.Unchecked)
            run_item.setToolTip("Include this source in the next batch run")
            self.queue.setItem(row, 0, run_item)
            video_item = QTableWidgetItem(entry.path.name)
            video_item.setData(Qt.UserRole, entry.id)
            video_item.setToolTip(str(entry.path))
            self.queue.setItem(row, 1, video_item)
            review_item = QTableWidgetItem()
            review_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            review_item.setCheckState(Qt.Checked if entry.manual_review else Qt.Unchecked)
            review_item.setToolTip("Pause after detection and open generated clips in the manual Video editor")
            self.queue.setItem(row, 2, review_item)
            status_item = QTableWidgetItem(entry.detail)
            status_item.setToolTip(entry.detail)
            self.queue.setItem(row, 3, status_item)
            progress = QProgressBar()
            progress.setRange(0, 100)
            progress.setValue(entry.progress)
            progress.setTextVisible(True)
            progress.setFormat(f"{entry.progress}%")
            progress.setToolTip("Analysis and export progress for this source")
            self.queue.setCellWidget(row, 4, progress)
            self.queue.setRowHeight(row, 34)
        self._building = was_building
        self._update_summary()
        self._update_actions()

    def _load_configuration_controls(self) -> None:
        self.output_folder.setText(self.configuration.output_dir)
        self.arrangement.setCurrentIndex(max(0, self.arrangement.findData(self.configuration.arrangement)))
        self.quality.setCurrentIndex(max(0, self.quality.findData(self.configuration.quality)))
        self.encoder.setCurrentIndex(max(0, self.encoder.findData(self.configuration.video_encoder)))
        self.include_audio.setChecked(self.configuration.include_audio)
        self.trash_source.setChecked(self.configuration.trash_after_success)

    def _sync_configuration(self) -> None:
        self.configuration.character_ids = self._selected_character_ids()
        self.configuration.output_dir = self.output_folder.text().strip()
        self.configuration.arrangement = str(self.arrangement.currentData())
        self.configuration.quality = str(self.quality.currentData())
        self.configuration.video_encoder = str(self.encoder.currentData())
        self.configuration.include_audio = self.include_audio.isChecked()
        self.configuration.trash_after_success = self.trash_source.isChecked()

    def _configuration_changed(self, *_args) -> None:
        if self._building:
            return
        self._sync_configuration()
        self._persist()
        self._update_summary()

    def _selected_character_ids(self) -> list[str]:
        return [
            str(self.characters.item(row).data(Qt.UserRole))
            for row in range(self.characters.count())
            if self.characters.item(row).checkState() == Qt.Checked
        ]

    def _entry_for_row(self, row: int) -> BatchVideoEntry | None:
        if row < 0 or row >= len(self.configuration.videos):
            return None
        return self.configuration.videos[row]

    def _persist(self) -> None:
        if self._building:
            return
        self._sync_configuration()
        try:
            save_batch_configuration(self.configuration)
        except OSError as exc:
            self.summary.setToolTip(f"Batch queue could not be saved: {exc}")

    def _update_summary(self) -> None:
        videos = sum(1 for entry in self.configuration.videos if entry.enabled)
        characters = len(self._selected_character_ids()) if hasattr(self, "characters") else 0
        self.summary.setText(
            f"{videos} video{'s' if videos != 1 else ''} · {characters} selected character{'s' if characters != 1 else ''}"
        )

    def _update_actions(self) -> None:
        running = bool(self.worker and self.worker.isRunning())
        selected = self.queue.currentRow() >= 0
        self.start_button.setEnabled(not running and bool(self.configuration.videos))
        self.pause_button.setEnabled(running and not self._review_entry_id)
        self.cancel_button.setEnabled(running)
        self.add_videos_button.setEnabled(not running)
        self.add_folder_button.setEnabled(not running)
        self.remove_button.setEnabled(not running and selected)
        self.open_editor_button.setEnabled(selected and (not running or bool(self._review_entry_id)))
        self.queue.setEnabled(not running or bool(self._review_entry_id))
