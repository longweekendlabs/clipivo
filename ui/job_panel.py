"""The Who → Output → Start panel shared by the Video and Batch tabs."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, QSettings, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.batch import SOURCE_AFTER
from core.character_store import CharacterStore
from core.face_engine import FaceEngine
from core.naming import DEFAULT_SCHEME, NAMING_SCHEMES, output_base_name

from .native_dialogs import choose_directory, choose_images


SAME_FOLDER = "__same_folder__"
CHOOSE_FOLDER = "__choose_folder__"


@dataclass(frozen=True)
class Person:
    id: str
    name: str
    one_time: bool = False


class OneTimePerson(QObject):
    """Photos of a person used only until Clipivo closes; nothing is written to disk."""

    ID = "__one_time__"
    NAME = "One-time person"
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.references: list[tuple[str, bytes]] = []

    def add(self, named_bytes: list[tuple[str, bytes]]) -> int:
        known = {sha256(data).digest() for _name, data in self.references}
        added = 0
        for name, data in named_bytes:
            digest = sha256(data).digest()
            if digest in known:
                continue
            known.add(digest)
            self.references.append((name, data))
            added += 1
        if added:
            self.changed.emit()
        return added

    def clear(self) -> None:
        if self.references:
            self.references = []
            self.changed.emit()

    def fingerprint(self) -> str:
        digest = sha256(b"one-time")
        for _name, data in self.references:
            digest.update(sha256(data).digest())
        return digest.hexdigest()


class _PhotoCheckWorker(QThread):
    checked = Signal(object, object)
    failed = Signal(str)

    def __init__(self, engine: FaceEngine, named_bytes: list[tuple[str, bytes]], parent=None):
        super().__init__(parent)
        self.engine = engine
        self.named_bytes = named_bytes

    def run(self) -> None:
        try:
            self.checked.emit(self.named_bytes, self.engine.assess_references(self.named_bytes, self.isInterruptionRequested))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class JobPanel(QWidget):
    """Pick who to find, how to save, and start. Progress shows in place; nothing blocks the window."""

    selectionChanged = Signal()
    settingsChanged = Signal()
    startRequested = Signal()
    cancelRequested = Signal()
    pauseRequested = Signal()
    applyToSelectedRequested = Signal()

    def __init__(
        self,
        store: CharacterStore | None,
        engine: FaceEngine | None,
        one_time: OneTimePerson,
        *,
        multiple: bool,
        settings_key: str,
        start_text: str,
        frame_provider: Callable[[], QPixmap] | None = None,
        show_source_after: bool = False,
        show_pause: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.store = store
        self.engine = engine
        self.one_time = one_time
        self.multiple = multiple
        self.settings_key = settings_key
        self.frame_provider = frame_provider
        self._start_text = start_text
        self._last_output_folder = ""
        self._custom_folder = ""
        self._source_folder = ""
        self._preview_source = Path("News broadcast-gvj96ngMgwY.mp4")
        self._check_worker: _PhotoCheckWorker | None = None
        self._one_time_listed = False
        self._loading = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self.who_header = QHBoxLayout()
        self.who_header.setContentsMargins(0, 0, 0, 0)
        who_title = QLabel("Who · for videos you add" if multiple else "Who")
        who_title.setObjectName("sectionTitle")
        self.who_header.addWidget(who_title)
        self.who_header.addStretch()
        layout.addLayout(self.who_header)
        if multiple:
            self.people_list = QListWidget()
            self.people_list.setObjectName("batchCharacters")
            self.people_list.setMinimumHeight(84)
            self.people_list.setMaximumHeight(150)
            self.people_list.setToolTip("Ticked people are given to videos you add next")
            self.people_list.itemChanged.connect(self._people_changed)
            self.people_list.currentItemChanged.connect(lambda *_args: self._update_photos())
            layout.addWidget(self.people_list)
            self.apply_button = QPushButton("Give to selected videos")
            self.apply_button.setToolTip("Replace who is looked for in the videos selected in the list")
            self.apply_button.clicked.connect(self.applyToSelectedRequested.emit)
            layout.addWidget(self.apply_button)
            self.person_combo = None
        else:
            self.person_combo = QComboBox()
            self.person_combo.setToolTip("Who Clipivo should find in this video")
            self.person_combo.currentIndexChanged.connect(self._people_changed)
            layout.addWidget(self.person_combo)
            self.people_list = None

        thumbs = QHBoxLayout()
        thumbs.setContentsMargins(0, 0, 0, 0)
        thumbs.setSpacing(4)
        self.photo_labels: list[QLabel] = []
        for _index in range(4):
            label = QLabel()
            label.setObjectName("targetThumbnail")
            label.setFixedSize(52, 52)
            label.setAlignment(Qt.AlignCenter)
            thumbs.addWidget(label)
            self.photo_labels.append(label)
        thumbs.addStretch()
        self.photos_row = QWidget()
        self.photos_row.setLayout(thumbs)
        layout.addWidget(self.photos_row)

        photo_actions = QHBoxLayout()
        photo_actions.setSpacing(4)
        self.add_photos_button = QPushButton("Add photos")
        self.add_photos_button.setObjectName("targetAction")
        self.add_photos_button.setToolTip("Add screenshots of a one-time person; they last until Clipivo closes")
        self.add_photos_button.clicked.connect(self._choose_photos)
        photo_actions.addWidget(self.add_photos_button)
        self.grab_frame_button = None
        if frame_provider is not None:
            self.grab_frame_button = QPushButton("Use this frame")
            self.grab_frame_button.setObjectName("targetAction")
            self.grab_frame_button.setToolTip("Add the frame shown in the player as a one-time photo")
            self.grab_frame_button.clicked.connect(self._grab_frame)
            photo_actions.addWidget(self.grab_frame_button)
        self.clear_photos_button = QPushButton("Clear")
        self.clear_photos_button.setToolTip("Forget the one-time person's photos")
        self.clear_photos_button.clicked.connect(self.one_time.clear)
        photo_actions.addWidget(self.clear_photos_button)
        layout.addLayout(photo_actions)
        self.who_hint = QLabel("")
        self.who_hint.setObjectName("targetHint")
        self.who_hint.setWordWrap(True)
        layout.addWidget(self.who_hint)

        output_title = QLabel("Output")
        output_title.setObjectName("sectionTitle")
        layout.addSpacing(4)
        layout.addWidget(output_title)
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(4)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.arrangement = QComboBox()
        self.arrangement.addItem("One stitched video", "compilation")
        self.arrangement.addItem("Separate clips", "separate")
        self.arrangement.setToolTip("Join every appearance into one file, or save each one on its own")
        form.addRow("Save as", self.arrangement)
        self.mode = QComboBox()
        self.mode.addItem("Original quality", "copy")
        self.mode.addItem("Exact frames", "accurate")
        self.mode.setItemData(
            0, "Copies the original video untouched, so quality is identical. "
            "Each clip starts at the keyframe just before, often a second or few early.", Qt.ToolTipRole,
        )
        self.mode.setItemData(
            1, "Re-encodes at high quality so clips start and end on the exact frame. "
            "Slower and the files are larger than the original.", Qt.ToolTipRole,
        )
        self.mode.setToolTip(self.mode.itemData(0, Qt.ToolTipRole))
        form.addRow("Quality", self.mode)
        self.destination = QComboBox()
        self.destination.setToolTip("Where finished videos are saved; names end with the date, time and person")
        self.destination.activated.connect(self._destination_activated)
        form.addRow("Save to", self.destination)
        self.naming = QComboBox()
        for key, label in NAMING_SCHEMES.items():
            self.naming.addItem(label, key)
        self.naming.setToolTip("How finished files are named. The video ID is read from names like 'Title-gvj96ngMgwY'.")
        form.addRow("Name", self.naming)
        self.custom_name = QLineEdit()
        self.custom_name.setPlaceholderText("news")
        self.custom_name.setToolTip("Your text at the start of each file name")
        self.custom_name_row = form.rowCount()
        form.addRow("Custom", self.custom_name)
        self._form = form
        self.source_after = None
        if show_source_after:
            self.source_after = QComboBox()
            self.source_after.setToolTip(
                "What happens to each original once all its outputs are saved and checked. "
                "Nothing happens to a video that failed, was cancelled, or where nobody was found."
            )
            self.source_after.activated.connect(self._source_after_activated)
            form.addRow("Original", self.source_after)
        for combo in (self.arrangement, self.mode, self.destination, self.naming, *([self.source_after] if self.source_after else [])):
            combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        layout.addLayout(form)
        self.name_preview = QLabel("")
        self.name_preview.setObjectName("targetHint")
        self.name_preview.setWordWrap(True)
        layout.addWidget(self.name_preview)
        self.include_audio = QCheckBox("Include audio")
        self.include_audio.setChecked(True)
        layout.addWidget(self.include_audio)

        run_row = QHBoxLayout()
        run_row.setSpacing(4)
        self.start_button = QPushButton(start_text)
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.startRequested.emit)
        run_row.addWidget(self.start_button, 1)
        self.pause_button = None
        if show_pause:
            self.pause_button = QPushButton("Pause")
            self.pause_button.clicked.connect(self.pauseRequested.emit)
            run_row.addWidget(self.pause_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancelRequested.emit)
        run_row.addWidget(self.cancel_button)
        layout.addSpacing(4)
        layout.addLayout(run_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        layout.addWidget(self.progress)
        status_row = QHBoxLayout()
        status_row.setSpacing(4)
        self.status = QLabel("")
        self.status.setObjectName("targetHint")
        self.status.setWordWrap(True)
        status_row.addWidget(self.status, 1)
        self.open_folder_button = QPushButton("Open folder")
        self.open_folder_button.setToolTip("Show the finished videos")
        self.open_folder_button.clicked.connect(self._open_output_folder)
        status_row.addWidget(self.open_folder_button, 0, Qt.AlignTop)
        layout.addLayout(status_row)

        for combo in (self.arrangement, self.mode, self.naming):
            combo.currentIndexChanged.connect(self._settings_changed)
        self.mode.currentIndexChanged.connect(lambda *_args: self.mode.setToolTip(self.mode.currentData(Qt.ToolTipRole)))
        self.naming.currentIndexChanged.connect(lambda *_args: self._update_naming())
        self.custom_name.textChanged.connect(lambda *_args: (self._update_naming(), self._settings_changed()))
        self.include_audio.toggled.connect(self._settings_changed)
        self.one_time.changed.connect(self.refresh_people)

        self._load_settings()
        self._update_naming()
        self.refresh_people()
        self.set_running(False)
        self.progress.hide()
        self.open_folder_button.hide()
        self._loading = False

    # People ---------------------------------------------------------------

    def refresh_people(self, select_id: str = "") -> None:
        characters = self.store.characters() if self.store else []
        people = [Person(item.id, item.name) for item in characters]
        if self.one_time.references:
            people.append(Person(OneTimePerson.ID, OneTimePerson.NAME, one_time=True))
        if self.multiple:
            checked = set(self.selected_ids()) | ({select_id} if select_id else set())
            current = self.people_list.currentItem().data(Qt.UserRole).id if self.people_list.currentItem() else ""
            self.people_list.blockSignals(True)
            self.people_list.clear()
            for person in people:
                item = QListWidgetItem(f"{person.name}  ·  {self._photo_count(person)} photos")
                item.setData(Qt.UserRole, person)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                newly_listed = person.one_time and not self._one_time_listed
                item.setCheckState(Qt.Checked if person.id in checked or newly_listed else Qt.Unchecked)
                self.people_list.addItem(item)
                if person.id == current:
                    self.people_list.setCurrentItem(item)
            self.people_list.blockSignals(False)
            self._one_time_listed = bool(self.one_time.references)
        else:
            previous = select_id or self.selected_id()
            self.person_combo.blockSignals(True)
            self.person_combo.clear()
            self.person_combo.addItem("Add photos to start" if not people else "Choose who to find…", None)
            for person in people:
                self.person_combo.addItem(f"{person.name}  ·  {self._photo_count(person)} photos", person)
            index = next(
                (row for row in range(self.person_combo.count()) if getattr(self.person_combo.itemData(row), "id", None) == previous),
                -1,
            )
            if index < 0 and self.one_time.references:
                index = self.person_combo.count() - 1
            self.person_combo.setCurrentIndex(max(0, index))
            self.person_combo.blockSignals(False)
        self._update_photos()
        if not self._loading:
            self.selectionChanged.emit()

    def select_person(self, person_id: str) -> None:
        if self.multiple or not person_id:
            return
        for row in range(self.person_combo.count()):
            if getattr(self.person_combo.itemData(row), "id", None) == person_id:
                self.person_combo.setCurrentIndex(row)
                return

    def selected_people(self) -> list[Person]:
        if self.multiple:
            return [
                self.people_list.item(row).data(Qt.UserRole)
                for row in range(self.people_list.count())
                if self.people_list.item(row).checkState() == Qt.Checked
            ]
        person = self.person_combo.currentData()
        return [person] if isinstance(person, Person) else []

    def selected_ids(self) -> list[str]:
        if self.people_list is None and self.person_combo is None:
            return []
        return [person.id for person in self.selected_people()]

    def selected_id(self) -> str:
        people = self.selected_people()
        return people[0].id if people else ""

    def references_for(self, person: Person) -> list[tuple[str, bytes]]:
        if person.one_time:
            return list(self.one_time.references)
        return list(self.store.iter_reference_bytes(person.id)) if self.store else []

    def fingerprint_for(self, person: Person) -> str:
        if person.one_time:
            return self.one_time.fingerprint()
        return self.store.fingerprint(person.id) if self.store else ""

    def _photo_count(self, person: Person) -> int:
        if person.one_time:
            return len(self.one_time.references)
        return len(self.store.references(person.id)) if self.store else 0

    def _people_changed(self, *_args) -> None:
        self._update_photos()
        if not self._loading:
            self.selectionChanged.emit()

    def _update_photos(self) -> None:
        if self.multiple:
            current = self.people_list.currentItem()
            person = current.data(Qt.UserRole) if current else None
            if person is None and self.one_time.references:
                person = Person(OneTimePerson.ID, OneTimePerson.NAME, one_time=True)
        else:
            person = self.person_combo.currentData()
        pixmaps: list[QPixmap] = []
        if isinstance(person, Person):
            for _name, data in self.references_for(person)[: len(self.photo_labels)]:
                pixmap = QPixmap()
                if pixmap.loadFromData(data):
                    pixmaps.append(pixmap)
        for index, label in enumerate(self.photo_labels):
            if index < len(pixmaps):
                label.setPixmap(pixmaps[index].scaled(label.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
                label.show()
            else:
                label.clear()
                label.hide()
        self.photos_row.setVisible(bool(pixmaps))
        self.clear_photos_button.setVisible(bool(self.one_time.references))
        if self._check_worker and self._check_worker.isRunning():
            return
        if self.one_time.references:
            self.who_hint.setText("One-time photos last until Clipivo closes. Save people for later in People.")
        elif self.store and self.store.characters():
            self.who_hint.setText("Pick a saved person, or add screenshots of someone new.")
        else:
            self.who_hint.setText("Add a few screenshots of the person. 5–15 varied shots work best.")

    def _choose_photos(self) -> None:
        paths = [Path(path) for path in choose_images(self, "Add photos of the person", key="one_time_photos")]
        self._check_and_add([(path.name, path.read_bytes()) for path in paths if path.is_file()])

    def _grab_frame(self) -> None:
        pixmap = self.frame_provider() if self.frame_provider else QPixmap()
        if pixmap.isNull():
            self.who_hint.setText("Open a video and pause on a clear shot of the person first.")
            return
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.WriteOnly)
        pixmap.save(buffer, "PNG")
        self._check_and_add([(f"frame-{len(self.one_time.references) + 1:02d}.png", bytes(data))])

    def _check_and_add(self, named_bytes: list[tuple[str, bytes]]) -> None:
        if not named_bytes:
            return
        if not self.engine:
            self.who_hint.setText("The face engine is unavailable, so photos can't be checked.")
            return
        if self._check_worker and self._check_worker.isRunning():
            return
        self.who_hint.setText(f"Checking {len(named_bytes)} photo{'s' if len(named_bytes) != 1 else ''} for a face…")
        self.add_photos_button.setEnabled(False)
        worker = _PhotoCheckWorker(self.engine, named_bytes, self)
        self._check_worker = worker
        worker.checked.connect(self._photos_checked)
        worker.failed.connect(self._photos_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _photos_checked(self, named_bytes: list[tuple[str, bytes]], results) -> None:
        self._check_worker = None
        self.add_photos_button.setEnabled(True)
        by_name = dict(named_bytes)
        usable = [(item.name, by_name[item.name]) for item in results if item.valid and item.name in by_name]
        skipped = len(named_bytes) - len(usable)
        added = self.one_time.add(usable)
        if not self.multiple:
            self.select_person(OneTimePerson.ID)
        message = f"Added {added} photo{'s' if added != 1 else ''}."
        if skipped:
            message += f" Skipped {skipped}: no clear face."
        self.who_hint.setText(message)

    def _photos_failed(self, detail: str) -> None:
        self._check_worker = None
        self.add_photos_button.setEnabled(True)
        self.who_hint.setText(f"Couldn't check photos: {detail}")

    # Output ---------------------------------------------------------------

    def values(self) -> dict:
        destination = self.destination.currentData()
        return {
            "arrangement": str(self.arrangement.currentData()),
            "mode": str(self.mode.currentData()),
            "save_next_to_source": destination == SAME_FOLDER,
            "output_dir": "" if destination == SAME_FOLDER else str(destination or ""),
            "include_audio": self.include_audio.isChecked(),
            "naming": str(self.naming.currentData()),
            "custom_name": self.custom_name.text().strip(),
            "source_after": str(self.source_after.currentData()) if self.source_after else "keep",
            "source_folder": self._source_folder,
        }

    def set_values(self, values: dict) -> None:
        self._loading = True
        self.arrangement.setCurrentIndex(max(0, self.arrangement.findData(values.get("arrangement", "compilation"))))
        self.mode.setCurrentIndex(max(0, self.mode.findData(values.get("mode", "accurate"))))
        self._custom_folder = str(values.get("output_dir", "") or "")
        self._rebuild_destinations(SAME_FOLDER if values.get("save_next_to_source", True) or not self._custom_folder else self._custom_folder)
        self.include_audio.setChecked(bool(values.get("include_audio", True)))
        self.naming.setCurrentIndex(max(0, self.naming.findData(values.get("naming", DEFAULT_SCHEME))))
        self.custom_name.setText(str(values.get("custom_name", "") or ""))
        if self.source_after:
            self._source_folder = str(values.get("source_folder", "") or "")
            self._rebuild_source_after(str(values.get("source_after", "keep")))
        self._update_naming()
        self._loading = False

    def set_copy_allowed(self, allowed: bool) -> None:
        """Filters need re-encoding; switch to exact frames without changing the saved preference."""
        model = self.mode.model()
        copy_row = self.mode.findData("copy")
        item = model.item(copy_row) if hasattr(model, "item") else None
        if item is not None:
            item.setEnabled(allowed)
        was_loading = self._loading
        self._loading = True
        if not allowed and self.mode.currentData() == "copy":
            self._forced_exact = True
            self.mode.setCurrentIndex(self.mode.findData("accurate"))
        elif allowed and getattr(self, "_forced_exact", False):
            self._forced_exact = False
            self.mode.setCurrentIndex(copy_row)
        self._loading = was_loading
        self.mode.setToolTip(
            self.mode.currentData(Qt.ToolTipRole) if allowed
            else "Active video filters need re-encoding, so original quality is off"
        )

    def _rebuild_destinations(self, select) -> None:
        self.destination.blockSignals(True)
        self.destination.clear()
        self.destination.addItem("Same folder as video", SAME_FOLDER)
        if self._custom_folder:
            self.destination.addItem(self._short_path(self._custom_folder), self._custom_folder)
            self.destination.setItemData(1, self._custom_folder, Qt.ToolTipRole)
        self.destination.addItem("Choose folder…", CHOOSE_FOLDER)
        index = self.destination.findData(select)
        self.destination.setCurrentIndex(max(0, index))
        self.destination.blockSignals(False)

    @staticmethod
    def _short_path(path: str) -> str:
        home = str(Path.home())
        return "~" + path[len(home):] if path.startswith(home) else path

    def _destination_activated(self, _index: int) -> None:
        if self.destination.currentData() == CHOOSE_FOLDER:
            folder = choose_directory(self, "Save finished videos to", key=f"{self.settings_key}_output_folder")
            if folder:
                self._custom_folder = folder
            self._rebuild_destinations(folder or (self._custom_folder or SAME_FOLDER))
        self._settings_changed()

    def _settings_changed(self, *_args) -> None:
        if self._loading:
            return
        self._save_settings()
        self.settingsChanged.emit()

    def _load_settings(self) -> None:
        settings = QSettings()
        prefix = f"{self.settings_key}/"
        self.set_values({
            "arrangement": settings.value(prefix + "arrangement", "compilation"),
            # A new key: 0.3.0 saved "mode" with a re-encoding default.
            "mode": settings.value(prefix + "quality", "copy"),
            "save_next_to_source": settings.value(prefix + "save_next_to_source", True, bool),
            "output_dir": settings.value(prefix + "output_dir", ""),
            "include_audio": settings.value(prefix + "include_audio", True, bool),
            "naming": settings.value(prefix + "naming", DEFAULT_SCHEME),
            "custom_name": settings.value(prefix + "custom_name", ""),
        })

    def _save_settings(self) -> None:
        settings = QSettings()
        prefix = f"{self.settings_key}/"
        values = self.values()
        for key in ("arrangement", "save_next_to_source", "include_audio", "naming", "custom_name"):
            settings.setValue(prefix + key, values[key])
        settings.setValue(prefix + "quality", values["mode"])
        settings.setValue(prefix + "output_dir", self._custom_folder)

    def set_preview_source(self, source: Path | None) -> None:
        if source:
            self._preview_source = Path(source)
        self._update_naming()

    def _update_naming(self) -> None:
        scheme = str(self.naming.currentData())
        custom = scheme.startswith("custom")
        self._form.setRowVisible(self.custom_name_row, custom)
        example = output_base_name(self._preview_source, scheme, self.custom_name.text())
        self.name_preview.setText(f"For example: {example}.mp4")
        self.name_preview.setToolTip(
            "Separate clips add _001, _002 and so on. When one video is searched for several people, "
            "each file also ends with the person's name."
        )

    def _rebuild_source_after(self, select: str) -> None:
        self.source_after.blockSignals(True)
        self.source_after.clear()
        for key, label in SOURCE_AFTER.items():
            if key == "folder" and self._source_folder:
                label = f"Move to {self._short_path(self._source_folder)}"
            self.source_after.addItem(label, key)
        if self._source_folder:
            self.source_after.addItem("Choose a different folder…", CHOOSE_FOLDER)
        self.source_after.setCurrentIndex(max(0, self.source_after.findData(select)))
        self.source_after.blockSignals(False)

    def _source_after_activated(self, _index: int) -> None:
        choice = self.source_after.currentData()
        if choice == CHOOSE_FOLDER or (choice == "folder" and not self._source_folder):
            folder = choose_directory(self, "Move finished originals to", key=f"{self.settings_key}_source_folder")
            if folder:
                self._source_folder = folder
                choice = "folder"
            else:
                choice = "folder" if self._source_folder else "keep"
            self._rebuild_source_after(choice)
        self._settings_changed()

    # Running --------------------------------------------------------------

    def set_running(self, running: bool, *, paused: bool = False) -> None:
        self.start_button.setEnabled(not running)
        self.start_button.setText("Working…" if running else self._start_text)
        self.cancel_button.setVisible(running)
        if self.pause_button:
            self.pause_button.setVisible(running)
            self.pause_button.setText("Resume" if paused else "Pause")
        for widget in (self.arrangement, self.mode, self.destination, self.include_audio, self.add_photos_button,
                       self.naming, self.custom_name, *([self.source_after] if self.source_after else [])):
            widget.setEnabled(not running)
        if self.people_list is not None:
            self.apply_button.setEnabled(not running)
        if self.person_combo is not None:
            self.person_combo.setEnabled(not running)
        if self.people_list is not None:
            self.people_list.setEnabled(not running)
        if self.grab_frame_button:
            self.grab_frame_button.setEnabled(not running)
        if running:
            self.progress.show()
            self.open_folder_button.hide()

    def show_progress(self, percent: int, text: str) -> None:
        self.progress.show()
        self.progress.setValue(max(0, min(100, int(percent))))
        self.status.setText(text)

    def show_finished(self, text: str, output_folder: str = "", *, success: bool = True) -> None:
        self.set_running(False)
        self.progress.setValue(100 if success else 0)
        self.progress.setVisible(success)
        self.status.setText(text)
        self.status.setToolTip(f"Saved to {output_folder}" if output_folder else "")
        self._last_output_folder = output_folder
        self.open_folder_button.setVisible(bool(output_folder))

    def clear_status(self) -> None:
        self.progress.hide()
        self.status.setText("")
        self.open_folder_button.hide()

    def _open_output_folder(self) -> None:
        if self._last_output_folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._last_output_folder))
