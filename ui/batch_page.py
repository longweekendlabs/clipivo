from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Event

from PySide6.QtCore import QSize, QThread, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.batch import (
    BatchCharacterTarget,
    BatchConfiguration,
    BatchVideoEntry,
    VIDEO_SUFFIXES,
    analyse_video_for_characters,
    discover_videos,
    load_batch_configuration,
    handle_finished_source,
    track_person_in_clips,
    save_batch_configuration,
    validate_export,
)
from core.character_store import CharacterStore
from core.export import ExportError, OutputShape, export_ranges, extract_cover, snap_to_keyframes
from core.naming import output_base_name
from core.face_engine import FaceEngine
from core.filters import VideoFilter
from core.models import ClipRange
from core.timecode import format_duration
from core.video_probe import probe_video

from .job_panel import JobPanel, OneTimePerson
from .native_dialogs import choose_directory, choose_videos


class BatchExecutionWorker(QThread):
    """Analyse and export queued videos. Used by Batch and, with one entry, by the Video tab's Start."""

    entryUpdated = Signal(str, str, int, str, object)
    rangesFound = Signal(str, str, str, object)
    allFinished = Signal(bool)

    def __init__(
        self,
        configuration: BatchConfiguration,
        targets: list[BatchCharacterTarget],
        face_engine: FaceEngine,
        parent=None,
        *,
        filters: list[VideoFilter] | None = None,
    ):
        super().__init__(parent)
        self.configuration = BatchConfiguration.from_dict(configuration.to_dict())
        self.configuration.save_next_to_source = configuration.save_next_to_source
        self.targets = targets
        self.face_engine = face_engine
        self.filters = [VideoFilter.from_dict(item.to_dict()) for item in (filters or [])]
        self._pause_event = Event()
        self._pause_event.set()
        self._cancel_event = Event()

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._pause_event.clear()
        else:
            self._pause_event.set()

    def cancel(self) -> None:
        self._cancel_event.set()
        self.requestInterruption()
        self._pause_event.set()

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
            except Exception as exc:  # noqa: BLE001
                if self._cancelled():
                    self.entryUpdated.emit(entry.id, "cancelled", entry.progress, "Cancelled · nothing was changed", [])
                    cancelled = True
                    break
                self.entryUpdated.emit(entry.id, "failed", entry.progress, str(exc), [])
        self.allFinished.emit(cancelled or self._cancelled())

    def _process_entry(self, entry: BatchVideoEntry) -> None:
        source = entry.path
        if not source.is_file():
            raise RuntimeError(f"Source file is missing: {source}")
        # Each video is searched only for the people chosen for it.
        targets = [target for target in self.targets if target.id in entry.person_ids]
        if not targets:
            raise RuntimeError("Nobody is chosen for this video")
        self.entryUpdated.emit(entry.id, "analysing", 1, "Reading video…", [])
        info = probe_video(source, include_keyframes=False)
        results = analyse_video_for_characters(
            source,
            self.face_engine,
            targets,
            duration_ms=info.duration_ms,
            cancelled=self._cancelled,
            wait_if_paused=self._wait_if_paused,
            progress=lambda current, total, label: self.entryUpdated.emit(
                entry.id, "analysing", min(70, round(current / max(1, total) * 70)), label, [],
            ),
            refine_progress=lambda done, total: self.entryUpdated.emit(
                entry.id, "analysing", 63 + round(done / max(1, total) * 7), f"Tightening clip edges {done + 1} of {total}", [],
            ),
        )
        if self._cancelled():
            raise ExportError("Cancelled.")
        if self.configuration.framing != "whole":
            for result in results:
                if not result.ranges:
                    continue
                target = next(item for item in targets if item.id == result.character_id)
                track_person_in_clips(
                    source, self.face_engine, target.id, self.face_engine.reference_matrix(target.references),
                    target.threshold, list(result.ranges), cancelled=self._cancelled, wait_if_paused=self._wait_if_paused,
                    progress=lambda done, total, name=result.character_name: self.entryUpdated.emit(
                        entry.id, "analysing", 70, f"Following {name} through each shot · {round(done / max(1, total) * 100)}%", [],
                    ),
                )
            if self._cancelled():
                raise ExportError("Cancelled.")
        for result in results:
            self.rangesFound.emit(
                entry.id, result.character_id, result.character_name,
                [ClipRange.from_dict(item.to_dict()) for item in result.ranges],
            )

        outputs: list[Path] = []
        exportable = [result for result in results if result.ranges]
        missing = [result.character_name for result in results if not result.ranges]
        if not exportable:
            self.entryUpdated.emit(entry.id, "no_match", 100, "Nobody found · source kept", [])
            return

        stitched = self.configuration.arrangement == "compilation"
        shape = OutputShape(
            self.configuration.output_quality if self.configuration.mode == "copy" else min(95, self.configuration.output_quality),
            self.configuration.max_short_side,
            self.configuration.framing,
            (int(getattr(info, "width", 0) or 0), int(getattr(info, "height", 0) or 0)),
        )
        filtered = any(item.enabled for item in self.filters)
        mode = "accurate" if filtered or shape.needs_reencode() else self.configuration.mode
        output_dir = self.configuration.output_dir_for(source)
        found_ms = 0
        wanted_names: dict[Path, str] = {}
        when = datetime.now()
        for index, result in enumerate(exportable):
            ranges = list(result.ranges)
            if self._cancelled():
                raise ExportError("Cancelled.")
            if mode == "copy":
                self.entryUpdated.emit(entry.id, "exporting", 70, "Finding keyframes for original-quality cuts", [])
                ranges = snap_to_keyframes(source, ranges)
            found_ms += sum(item.duration_ms for item in ranges)
            output_dir.mkdir(parents=True, exist_ok=True)
            person = "" if len(targets) == 1 or result.character_id == OneTimePerson.ID else result.character_name
            base_name = output_base_name(
                source, self.configuration.naming, self.configuration.custom_name, person, when,
            )
            base = 70 + round(index / len(exportable) * 25)
            span = max(1, round(25 / len(exportable)))
            character_outputs = export_ranges(
                source,
                ranges,
                output_dir,
                mode,
                stitched,
                include_audio=self.configuration.include_audio,
                progress=lambda done, total, _text, base=base, width=span, name=result.character_name: self.entryUpdated.emit(
                    entry.id,
                    "exporting",
                    min(95, base + round(done / max(1, total) * width)),
                    f"Saving {name} · {round(done / max(1, total) * 100)}%",
                    [str(path) for path in outputs],
                ),
                cancel_event=self._cancel_event,
                filters=self.filters,
                source_fps=info.fps,
                video_encoder=self.configuration.video_encoder,
                output_character=result.character_name,
                source_video_bitrate_kbps=getattr(info, "video_bitrate_kbps", 0),
                output_name=base_name,
                shape=shape,
            )
            expected = [sum(item.duration_ms for item in ranges)] if stitched else [item.duration_ms for item in ranges]
            for position, (output, expected_duration) in enumerate(zip(character_outputs, expected, strict=True), start=1):
                try:
                    validate_export(output, expected_duration_ms=expected_duration)
                except Exception:
                    output.unlink(missing_ok=True)
                    raise
                suffix = "" if len(character_outputs) == 1 else f"_{position:03d}"
                wanted_names[output] = f"{base_name}{suffix}{output.suffix}"
            outputs.extend(character_outputs)

        clips = sum(len(result.ranges) for result in exportable)
        detail = f"Done · {clips} clip{'s' if clips != 1 else ''} · {format_duration(found_ms)}"
        if missing:
            detail += f" · not found: {', '.join(missing)} · source kept"
        elif self.configuration.source_after != "keep":
            self.entryUpdated.emit(entry.id, "exporting", 98, "Saved and checked · moving the original", [str(path) for path in outputs])
            moved = handle_finished_source(source, self.configuration.source_after, self.configuration.source_folder)
            if moved:
                detail += f" · {moved}"
            outputs = [_take_wanted_name(path, wanted_names.get(path, path.name)) for path in outputs]
        self.entryUpdated.emit(entry.id, "completed", 100, detail, [str(path) for path in outputs])

    def _wait_if_paused(self) -> bool:
        while not self._pause_event.wait(0.1):
            if self._cancelled():
                return False
        return not self._cancelled()

    def _cancelled(self) -> bool:
        return self._cancel_event.is_set() or self.isInterruptionRequested()


def _take_wanted_name(path: Path, wanted: str) -> Path:
    """Once the original has moved away, an output can take the name it was meant to have."""
    target = path.with_name(wanted)
    if target == path or target.exists():
        return path
    path.rename(target)
    return target


class _CoverWorker(QThread):
    """Pulls embedded cover pictures (YouTube thumbnails) for queue rows, one file at a time."""

    found = Signal(str, str)

    def __init__(self, paths: list[str], parent=None):
        super().__init__(parent)
        self.paths = paths

    def run(self) -> None:
        for path in self.paths:
            if self.isInterruptionRequested():
                return
            try:
                cover = extract_cover(Path(path), max_width=160)
            except OSError:
                cover = None
            self.found.emit(path, str(cover) if cover else "")


class _QueueTable(QTableWidget):
    """The video list; accepts videos and folders dropped from a file manager."""

    pathsDropped = Signal(object)

    def __init__(self, *args):
        super().__init__(*args)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QTableWidget.DropOnly)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if not event.mimeData().hasUrls():
            super().dropEvent(event)
            return
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        event.acceptProposedAction()
        self.pathsDropped.emit(paths)


class BatchPage(QWidget):
    openVideoRequested = Signal(object)
    runningChanged = Signal(bool)

    def __init__(
        self,
        character_store: CharacterStore | None,
        face_engine: FaceEngine | None,
        parent=None,
        *,
        one_time: OneTimePerson | None = None,
    ):
        super().__init__(parent)
        self.character_store = character_store
        self.face_engine = face_engine
        self.one_time = one_time or OneTimePerson(self)
        self.configuration = load_batch_configuration()
        self.worker: BatchExecutionWorker | None = None
        self.match_threshold = 0.40
        self.video_encoder = "auto"
        self.busy_elsewhere = lambda: False
        self._paused = False
        self._foreground_busy = False
        self._covers: dict[str, QIcon | None] = {}
        self._cover_workers: set[_CoverWorker] = set()
        self._building = True
        self._build_ui()
        self.job.set_values(self.configuration.to_dict())
        self.job.refresh_people()
        for row in range(self.job.people_list.count()):
            item = self.job.people_list.item(row)
            if item.data(Qt.UserRole).id in self.configuration.character_ids:
                item.setCheckState(Qt.Checked)
        self.one_time.changed.connect(self._populate_queue)
        self._populate_queue()
        self._building = False
        self._update_actions()

    def _build_ui(self) -> None:
        self.setAcceptDrops(True)
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(5)

        heading = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel("Batch")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "Drop videos or folders here. Each video is searched only for the people in its Who column, then Start."
        )
        subtitle.setObjectName("pageSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        heading.addLayout(title_box)
        heading.addStretch()
        self.summary = QLabel("0 videos")
        self.summary.setObjectName("batchSummary")
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
        self.add_videos_button.clicked.connect(self._choose_videos)
        self.add_folder_button = QPushButton("Add folder")
        self.add_folder_button.setToolTip("Add every video in a folder and its subfolders")
        self.add_folder_button.clicked.connect(self._choose_folder)
        self.remove_button = QPushButton("Remove")
        self.remove_button.setToolTip("Take the selected videos off the list; files are not touched")
        self.remove_button.clicked.connect(self._remove_selected)
        self.clear_done_button = QPushButton("Clear finished")
        self.clear_done_button.setToolTip("Take finished videos off the list")
        self.clear_done_button.clicked.connect(self._clear_finished)
        self.open_editor_button = QPushButton("Open in editor")
        self.open_editor_button.setToolTip("Open the selected video in the Video tab")
        self.open_editor_button.clicked.connect(self._open_selected)
        for button in (self.add_videos_button, self.add_folder_button, self.remove_button, self.clear_done_button, self.open_editor_button):
            tools.addWidget(button)
        tools.addStretch()
        queue_layout.addLayout(tools)

        self.queue = _QueueTable(0, 5)
        self.queue.setObjectName("batchQueue")
        self.queue.setHorizontalHeaderLabels(("Run", "Video", "Who", "Status", "Progress"))
        self.queue.verticalHeader().hide()
        self.queue.setIconSize(QSize(64, 36))
        self.queue.setSelectionBehavior(QTableWidget.SelectRows)
        self.queue.setSelectionMode(QTableWidget.ExtendedSelection)
        self.queue.setAlternatingRowColors(True)
        self.queue.setToolTip("Drop videos or folders here. Double-click a video to open it in the Video tab.")
        self.queue.horizontalHeader().resizeSection(0, 42)
        self.queue.horizontalHeader().resizeSection(2, 190)
        self.queue.horizontalHeader().resizeSection(3, 280)
        self.queue.horizontalHeader().resizeSection(4, 110)
        self.queue.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.queue.itemChanged.connect(self._queue_item_changed)
        self.queue.itemDoubleClicked.connect(lambda _item: self._open_selected())
        self.queue.itemSelectionChanged.connect(self._update_actions)
        self.queue.pathsDropped.connect(self.add_dropped)
        queue_layout.addWidget(self.queue, 1)
        self.empty_hint = QLabel("Drop videos or folders here, or use Add videos.")
        self.empty_hint.setObjectName("pageSubtitle")
        self.empty_hint.setAlignment(Qt.AlignCenter)
        queue_layout.addWidget(self.empty_hint)
        splitter.addWidget(queue_panel)

        config_panel = QWidget()
        config_panel.setObjectName("batchInspector")
        config_panel.setMinimumWidth(300)
        config_panel.setMaximumWidth(360)
        config_layout = QVBoxLayout(config_panel)
        config_layout.setContentsMargins(8, 2, 2, 2)
        self.job = JobPanel(
            self.character_store,
            self.face_engine,
            self.one_time,
            multiple=True,
            settings_key="batch_job",
            start_text="Start batch",
            show_source_after=True,
            show_pause=True,
        )
        self.job.startRequested.connect(self._start)
        self.job.cancelRequested.connect(self._cancel)
        self.job.pauseRequested.connect(self._toggle_pause)
        self.job.applyToSelectedRequested.connect(self._apply_people_to_selected)
        self.job.settingsChanged.connect(self._configuration_changed)
        self.job.selectionChanged.connect(self._configuration_changed)
        config_layout.addWidget(self.job)
        config_layout.addStretch()
        splitter.addWidget(config_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([900, 330])

    # Compatibility helpers used by the main window and tests.
    @property
    def characters(self):
        return self.job.people_list

    def refresh_characters(self) -> None:
        self.job.refresh_people()
        self._populate_queue()

    def set_match_threshold(self, threshold: float) -> None:
        self.match_threshold = max(0.0, min(1.0, float(threshold)))

    def set_video_encoder(self, encoder: str) -> None:
        self.video_encoder = encoder

    def add_paths(self, paths: list[Path]) -> int:
        """Queue videos for the people currently ticked on the right. Returns how many were added."""
        known = {str(Path(entry.source).resolve()) for entry in self.configuration.videos}
        people = self.job.selected_ids()
        added = 0
        for path in paths:
            path = Path(path)
            if not path.is_file() or path.suffix.lower() not in VIDEO_SUFFIXES:
                continue
            resolved = str(path.resolve())
            if resolved in known:
                continue
            self.configuration.videos.append(BatchVideoEntry(resolved, person_ids=list(people)))
            known.add(resolved)
            added += 1
        if added:
            self._populate_queue()
            self._persist()
        return added

    def add_dropped(self, paths: list[Path]) -> None:
        if self.is_running():
            return
        videos: list[Path] = []
        for path in paths:
            videos.extend(discover_videos(path) if path.is_dir() else [path])
        added = self.add_paths(videos)
        if not added:
            self.job.status.setText("Nothing new to add: drop video files or folders that contain them.")

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        self.add_dropped([Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()])
        event.acceptProposedAction()

    def set_foreground_busy(self, busy: bool) -> None:
        self._foreground_busy = bool(busy)
        if self.worker and self.worker.isRunning():
            self.worker.set_paused(self._paused or self._foreground_busy)

    def is_running(self) -> bool:
        return bool(self.worker and self.worker.isRunning())

    def _choose_videos(self) -> None:
        self.add_paths([Path(path) for path in choose_videos(self)])

    def _choose_folder(self) -> None:
        folder = choose_directory(self, "Add videos from folder", key="batch_source_folder")
        if folder:
            self.add_paths(discover_videos(Path(folder)))

    def _selected_entries(self) -> list[BatchVideoEntry]:
        rows = sorted({index.row() for index in self.queue.selectionModel().selectedRows()})
        return [entry for entry in (self._entry_for_row(row) for row in rows) if entry]

    def _remove_selected(self) -> None:
        selected = {entry.id for entry in self._selected_entries()}
        if not selected:
            return
        self.configuration.videos = [entry for entry in self.configuration.videos if entry.id not in selected]
        self._populate_queue()
        self._persist()

    def _clear_finished(self) -> None:
        self.configuration.videos = [
            entry for entry in self.configuration.videos if entry.status not in {"completed", "no_match"}
        ]
        self._populate_queue()
        self._persist()

    def _open_selected(self) -> None:
        entry = self._entry_for_row(self.queue.currentRow())
        if entry:
            self.openVideoRequested.emit(entry.path)

    def _apply_people_to_selected(self) -> None:
        entries = self._selected_entries()
        if not entries:
            self.job.status.setText("Select videos in the list first, then give them the ticked people.")
            return
        people = self.job.selected_ids()
        for entry in entries:
            entry.person_ids = list(people)
        self._populate_queue()
        self._persist()

    # People per video ------------------------------------------------------

    def _known_people(self) -> dict[str, str]:
        people = {item.id: item.name for item in (self.character_store.characters() if self.character_store else [])}
        if self.one_time.references:
            people[OneTimePerson.ID] = OneTimePerson.NAME
        return people

    def _people_label(self, entry: BatchVideoEntry) -> tuple[str, bool]:
        known = self._known_people()
        names = [known[person_id] for person_id in entry.person_ids if person_id in known]
        missing = len(entry.person_ids) - len(names)
        if not names:
            return ("Choose who…" if not missing else "Person missing · choose who…"), False
        label = ", ".join(names)
        if missing:
            label += " · 1 missing" if missing == 1 else f" · {missing} missing"
        return label, not missing

    def _who_button(self, entry: BatchVideoEntry) -> QToolButton:
        button = QToolButton()
        button.setObjectName("whoCell")
        button.setPopupMode(QToolButton.InstantPopup)
        button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        label, ok = self._people_label(entry)
        button.setText(label)
        button.setProperty("missing", not ok)
        button.setToolTip("Only these people are looked for in this video. Click to change.")
        menu = QMenu(button)
        for person_id, name in self._known_people().items():
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(person_id in entry.person_ids)
            action.toggled.connect(lambda checked, entry=entry, person_id=person_id: self._toggle_person(entry, person_id, checked))
        if menu.isEmpty():
            menu.addAction("Add a saved person in People, or photos on the right").setEnabled(False)
        button.setMenu(menu)
        return button

    def _toggle_person(self, entry: BatchVideoEntry, person_id: str, checked: bool) -> None:
        if checked and person_id not in entry.person_ids:
            entry.person_ids.append(person_id)
        elif not checked and person_id in entry.person_ids:
            entry.person_ids.remove(person_id)
        row = self.configuration.videos.index(entry)
        button = self.queue.cellWidget(row, 2)
        if isinstance(button, QToolButton):
            label, ok = self._people_label(entry)
            button.setText(label)
            button.setProperty("missing", not ok)
            button.style().unpolish(button)
            button.style().polish(button)
        self._persist()
        self._update_summary()

    # Running ---------------------------------------------------------------

    def _targets(self, person_ids: set[str]) -> list[BatchCharacterTarget] | None:
        known = self._known_people()
        targets: list[BatchCharacterTarget] = []
        for person_id in person_ids:
            if person_id == OneTimePerson.ID:
                references = list(self.one_time.references)
            else:
                references = list(self.character_store.iter_reference_bytes(person_id)) if self.character_store else []
            if not references:
                QMessageBox.information(self, "No photos", f"{known.get(person_id, 'A person')} has no photos yet.")
                return None
            targets.append(BatchCharacterTarget(person_id, known[person_id], references, self.match_threshold))
        return targets

    def _start(self) -> None:
        if self.is_running():
            return
        if self.busy_elsewhere():
            QMessageBox.information(self, "Clipivo is busy", "Wait for the Video tab job to finish, or cancel it, first.")
            return
        self._sync_configuration()
        entries = [entry for entry in self.configuration.videos if entry.enabled and entry.status != "completed"]
        if not entries:
            QMessageBox.information(self, "Nothing to do", "Add videos first. Finished videos are skipped.")
            return
        known = self._known_people()
        unassigned = [
            entry for entry in entries
            if not entry.person_ids or any(person_id not in known for person_id in entry.person_ids)
        ]
        if unassigned:
            names = "\n".join(f"• {entry.path.name}" for entry in unassigned[:8])
            more = f"\n…and {len(unassigned) - 8} more" if len(unassigned) > 8 else ""
            QMessageBox.information(
                self,
                "Choose who to find",
                f"These videos have nobody chosen, or a person who no longer exists:\n\n{names}{more}\n\n"
                "Set them in the Who column, or select them and use Give to selected videos.",
            )
            return
        if not self.character_store or not self.face_engine:
            QMessageBox.critical(self, "Batch unavailable", "The local face engine or character store is unavailable.")
            return
        targets = self._targets({person_id for entry in entries for person_id in entry.person_ids})
        if targets is None:
            return
        if self.configuration.source_after == "trash" and QMessageBox.question(
            self,
            "Move finished originals to Trash?",
            "After every output for a video is saved and checked, Clipivo moves that original to the Trash. "
            "Nothing is deleted outright; you can restore it from the Trash.\n\nContinue?",
        ) != QMessageBox.Yes:
            return
        run_ids = {entry.id for entry in entries}
        for entry in self.configuration.videos:
            if entry.id in run_ids:
                entry.status, entry.progress, entry.detail, entry.outputs = "queued", 0, "Waiting", []
        self._populate_queue()
        configuration = BatchConfiguration.from_dict(self.configuration.to_dict())
        configuration.videos = [entry for entry in configuration.videos if entry.id in run_ids]
        configuration.video_encoder = self.video_encoder
        factory = getattr(self.face_engine, "wide_shot_engine", None)
        engine = factory() if callable(factory) else self.face_engine
        self.worker = BatchExecutionWorker(configuration, targets, engine, self)
        worker = self.worker
        worker.entryUpdated.connect(self._entry_updated)
        worker.allFinished.connect(self._batch_finished)
        worker.finished.connect(self._worker_stopped)
        worker.finished.connect(worker.deleteLater)
        self._paused = False
        self.job.set_running(True)
        self.job.show_progress(0, f"Starting {len(entries)} video{'s' if len(entries) != 1 else ''}…")
        self.runningChanged.emit(True)
        self._update_actions()
        worker.start()

    def _toggle_pause(self) -> None:
        if not self.is_running():
            return
        self._paused = not self._paused
        self.worker.set_paused(self._paused or self._foreground_busy)
        self.job.set_running(True, paused=self._paused)

    def _cancel(self) -> None:
        if self.is_running():
            self.worker.cancel()

    def _entry_updated(self, entry_id: str, status: str, progress: int, detail: str, outputs: list[str]) -> None:
        entry = next((item for item in self.configuration.videos if item.id == entry_id), None)
        if not entry:
            return
        entry.status, entry.progress, entry.detail = status, progress, detail
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
        active = [item for item in self.configuration.videos if item.status in {"queued", "analysing", "exporting"}]
        done = [item for item in self.configuration.videos if item.status in {"completed", "no_match", "failed"}]
        overall = round((len(done) * 100 + sum(item.progress for item in active if item.status != "queued")) / max(1, len(done) + len(active)))
        self.job.show_progress(overall, f"{entry.path.name}: {detail}")
        self._persist()

    def _batch_finished(self, cancelled: bool) -> None:
        self.runningChanged.emit(False)
        self._paused = False
        if cancelled:
            for entry in self.configuration.videos:
                if entry.status in {"queued", "analysing", "exporting"}:
                    entry.status, entry.detail = "cancelled", "Cancelled · nothing was changed"
            self._populate_queue()
        finished = [entry for entry in self.configuration.videos if entry.status == "completed"]
        failed = [entry for entry in self.configuration.videos if entry.status == "failed"]
        folder = ""
        if finished and finished[-1].outputs:
            folder = str(Path(finished[-1].outputs[0]).parent)
        if cancelled:
            text = "Cancelled. Finished videos are kept."
        else:
            text = f"Done · {len(finished)} video{'s' if len(finished) != 1 else ''} saved"
            if failed:
                text += f" · {len(failed)} failed (see list)"
        self.job.show_finished(text, folder, success=not cancelled)
        self._persist()
        self._update_actions()

    def _worker_stopped(self) -> None:
        self.worker = None
        self._update_actions()

    def _queue_item_changed(self, item: QTableWidgetItem) -> None:
        if self._building or item.column() != 0:
            return
        entry = self._entry_for_row(item.row())
        if entry:
            entry.enabled = item.checkState() == Qt.Checked
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
            run_item.setToolTip("Include this video when you press Start")
            self.queue.setItem(row, 0, run_item)
            video_item = QTableWidgetItem(entry.path.name)
            video_item.setData(Qt.UserRole, entry.id)
            video_item.setToolTip(str(entry.path))
            icon = self._covers.get(entry.source)
            if icon is not None:
                video_item.setIcon(icon)
            self.queue.setItem(row, 1, video_item)
            self.queue.setCellWidget(row, 2, self._who_button(entry))
            detail = "Waiting" if entry.detail == "Ready" else entry.detail
            status_item = QTableWidgetItem(detail)
            status_item.setToolTip(detail)
            self.queue.setItem(row, 3, status_item)
            progress = QProgressBar()
            progress.setRange(0, 100)
            progress.setValue(entry.progress)
            progress.setFormat(f"{entry.progress}%")
            self.queue.setCellWidget(row, 4, progress)
            self.queue.setRowHeight(row, 40)
        self._building = was_building
        self._request_covers()
        self.empty_hint.setVisible(not self.configuration.videos)
        self._update_summary()
        self._update_actions()

    def _request_covers(self) -> None:
        missing = [entry.source for entry in self.configuration.videos if entry.source not in self._covers]
        if not missing:
            return
        for path in missing:
            self._covers[path] = None  # requested; filled in when found
        worker = _CoverWorker(missing, self)
        self._cover_workers.add(worker)
        worker.found.connect(self._cover_found)
        worker.finished.connect(lambda: (self._cover_workers.discard(worker), worker.deleteLater()))
        worker.start()

    def _cover_found(self, path: str, cover: str) -> None:
        if not cover:
            return
        icon = QIcon(QPixmap(cover))
        self._covers[path] = icon
        for row, entry in enumerate(self.configuration.videos):
            item = self.queue.item(row, 1)
            if entry.source == path and item is not None:
                item.setIcon(icon)

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)

    def shutdown(self) -> None:
        for worker in list(self._cover_workers):
            worker.requestInterruption()
            worker.wait(5000)

    def _sync_configuration(self) -> None:
        values = self.job.values()
        self.configuration.character_ids = self.job.selected_ids()
        self.configuration.output_dir = values["output_dir"]
        self.configuration.save_next_to_source = values["save_next_to_source"]
        self.configuration.save_into_done = values["save_into_done"]
        self.configuration.arrangement = values["arrangement"]
        self.configuration.mode = values["mode"]
        self.configuration.include_audio = values["include_audio"]
        self.configuration.naming = values["naming"]
        self.configuration.custom_name = values["custom_name"]
        self.configuration.source_after = values["source_after"]
        self.configuration.source_folder = values["source_folder"]
        self.configuration.output_quality = values["output_quality"]
        self.configuration.max_short_side = values["max_short_side"]
        self.configuration.framing = values["framing"]

    def _configuration_changed(self, *_args) -> None:
        if self._building:
            return
        self._persist()
        self._update_summary()

    def _selected_character_ids(self) -> list[str]:
        return self.job.selected_ids()

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
            self.summary.setToolTip(f"Batch list could not be saved: {exc}")

    def _update_summary(self) -> None:
        enabled = [entry for entry in self.configuration.videos if entry.enabled]
        known = self._known_people() if hasattr(self, "job") else {}
        counts: dict[str, int] = {}
        for entry in enabled:
            for person_id in entry.person_ids:
                if person_id in known:
                    counts[known[person_id]] = counts.get(known[person_id], 0) + 1
        unassigned = sum(1 for entry in enabled if not any(person_id in known for person_id in entry.person_ids))
        parts = [f"{len(enabled)} video{'s' if len(enabled) != 1 else ''}"]
        parts += [f"{name} {count}" for name, count in counts.items()]
        if unassigned:
            parts.append(f"{unassigned} without anyone")
        self.summary.setText(" · ".join(parts))

    def _update_actions(self) -> None:
        running = self.is_running()
        selected = self.queue.currentRow() >= 0
        self.add_videos_button.setEnabled(not running)
        self.add_folder_button.setEnabled(not running)
        self.remove_button.setEnabled(not running and selected)
        self.clear_done_button.setEnabled(not running and any(
            entry.status in {"completed", "no_match"} for entry in self.configuration.videos
        ))
        self.open_editor_button.setEnabled(selected)
        self.queue.setEnabled(not running)
