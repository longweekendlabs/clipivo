from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QSize, Qt, QThread, Signal
from PySide6.QtGui import QAction, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.character_store import CharacterRecord, CharacterStore, IMAGE_EXTENSIONS
from core.face_engine import FaceEngine, MatchAssessment, ReferenceAssessment

from .native_dialogs import choose_directory, choose_images
from .icons import line_icon
from .styles import PALETTE


CHARACTER_ROLE = Qt.UserRole + 1
REFERENCE_ROLE = Qt.UserRole + 2


def discover_character_folders(root: Path) -> list[tuple[str, list[Path]]]:
    """Map immediate subfolders to recursive image collections."""
    root = Path(root)
    discovered: list[tuple[str, list[Path]]] = []
    for folder in sorted(root.iterdir(), key=lambda path: path.name.casefold()):
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        images = [
            path for path in sorted(folder.rglob("*"), key=lambda path: str(path).casefold())
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        ]
        if images:
            discovered.append((folder.name, images))
    return discovered


class FaceTaskWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, task: Callable[[Callable[[], bool]], object], parent=None):
        super().__init__(parent)
        self.task = task

    def run(self) -> None:
        try:
            result = self.task(self.isInterruptionRequested)
            if self.isInterruptionRequested():
                self.cancelled.emit()
            else:
                self.succeeded.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))

    def cancel(self) -> None:
        self.requestInterruption()


class PeoplePage(QWidget):
    referencesChanged = Signal(object)
    characterDeleted = Signal(str)

    def __init__(
        self,
        store: CharacterStore | None,
        face_engine: FaceEngine | None,
        store_error: str = "",
        frame_provider: Callable[[], QPixmap] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.store = store
        self.face_engine = face_engine
        self.store_error = store_error
        self.frame_provider = frame_provider
        self.project_id = ""
        self.video_name = ""
        self.current_character: CharacterRecord | None = None
        self.match_threshold = 0.40
        self._worker: FaceTaskWorker | None = None
        self._progress: QProgressDialog | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(7)
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("People")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Save people you'll look for again. Their photos are stored encrypted on this computer.")
        subtitle.setObjectName("pageSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch()
        self.engine_status = QLabel("Face engine not checked")
        self.engine_status.setObjectName("engineStatus")
        self.engine_status.setToolTip("Recognition device and model availability")
        header.addWidget(self.engine_status)
        root.addLayout(header)

        if store_error:
            warning = QLabel(store_error)
            warning.setObjectName("warningBanner")
            warning.setWordWrap(True)
            warning.setToolTip("Why Clipivo could not unlock its encrypted character store")
            root.addWidget(warning)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setToolTip("Drag to resize the character library and reference editor")
        root.addWidget(splitter, 1)

        library = QWidget()
        library_layout = QVBoxLayout(library)
        library_layout.setContentsMargins(0, 0, 5, 0)
        reusable_group = QGroupBox("Saved people")
        reusable_layout = QVBoxLayout(reusable_group)
        reusable_toolbar = QHBoxLayout()
        reusable_toolbar.setSpacing(4)
        self.new_button = self._button("New person", "Save a new person you can pick in Video and Batch", self._new_character)
        self.new_button.setObjectName("primaryCompact")
        self.batch_button = self._button(
            "Batch import",
            "Choose a folder; each subfolder of photos becomes a saved person named after it",
            self._batch_import,
        )
        self.batch_button.setIcon(line_icon("open", size=16))
        self.export_all_button = self._button(
            "Export all",
            "Save every person's photos as plain images, one folder per person (the layout Batch import reads)",
            self._export_all,
        )
        reusable_toolbar.addWidget(self.new_button)
        reusable_toolbar.addWidget(self.batch_button)
        reusable_toolbar.addWidget(self.export_all_button)
        reusable_toolbar.addStretch()
        self.rename_action = QAction("Rename…", self)
        self.rename_action.triggered.connect(self._rename_character)
        self.delete_action = QAction("Delete…", self)
        self.delete_action.triggered.connect(self._delete_character)
        reusable_layout.addLayout(reusable_toolbar)
        self.character_list = QListWidget()
        self.character_list.setObjectName("characterList")
        self.character_list.setToolTip("Select a person to see and edit their photos")
        self.character_list.currentItemChanged.connect(self._persistent_selected)
        self.character_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.character_list.customContextMenuRequested.connect(self._character_context_menu)
        reusable_layout.addWidget(self.character_list, 1)
        library_layout.addWidget(reusable_group, 1)

        splitter.addWidget(library)

        editor = QWidget()
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(5, 0, 0, 0)
        selection_header = QHBoxLayout()
        self.selection_title = QLabel("Select a person")
        self.selection_title.setObjectName("sectionTitle")
        self.selection_title.setToolTip("Character currently being edited")
        selection_header.addWidget(self.selection_title)
        selection_header.addStretch()
        self.export_button = self._button("Export photos", "Save this person's photos as plain images in a folder", self._export_current)
        selection_header.addWidget(self.export_button)
        self.rename_button = self._button("Rename", "Rename this person", self._rename_character)
        self.delete_button = self._button("Delete person", "Delete this person and all their photos", self._delete_character)
        selection_header.addWidget(self.rename_button)
        selection_header.addWidget(self.delete_button)
        editor_layout.addLayout(selection_header)

        reference_toolbar = QHBoxLayout()
        reference_toolbar.setSpacing(4)
        self.add_references_button = QToolButton()
        self.add_references_button.setObjectName("referenceAdd")
        self.add_references_button.setText("Add photos")
        self.add_references_button.setIcon(line_icon("open", size=16))
        self.add_references_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.add_references_button.setPopupMode(QToolButton.InstantPopup)
        self.add_references_button.setToolTip("Add photos, a recursive folder, or the current video frame")
        add_menu = QMenu(self.add_references_button)
        self.add_photos_action = QAction("Choose photos…", self)
        self.add_photos_action.setToolTip("Validate and add one or more face reference images")
        self.add_photos_action.triggered.connect(self._add_photos)
        self.add_folder_action = QAction("Add a folder of photos…", self)
        self.add_folder_action.setToolTip("Recursively add supported images from one folder to the selected character")
        self.add_folder_action.triggered.connect(self._add_folder)
        self.capture_action = QAction("Capture current video frame", self)
        self.capture_action.setToolTip("Use the currently displayed video frame as a reference")
        self.capture_action.triggered.connect(self._capture_frame)
        for action in (self.add_photos_action, self.add_folder_action, self.capture_action):
            add_menu.addAction(action)
        self.add_references_button.setMenu(add_menu)
        reference_toolbar.addWidget(self.add_references_button)
        self.delete_reference_button = self._button("Remove", "Delete the selected encrypted reference image", self._delete_reference)
        reference_toolbar.addWidget(self.delete_reference_button)
        reference_toolbar.addSpacing(8)
        self.validate_button = self._button("Check photos", "Look for photos without a face or that don't look like the others", self._validate_set)
        self.test_button = self._button("Test photo", "Compare a still image against this character's reference set", self._test_photo)
        reference_toolbar.addWidget(self.validate_button)
        reference_toolbar.addWidget(self.test_button)
        reference_toolbar.addStretch()
        self.validation_summary = QLabel("5–15 varied photos work best.")
        self.validation_summary.setObjectName("referenceHint")
        self.validation_summary.setToolTip("Reference validation and consistency result")
        reference_toolbar.addWidget(self.validation_summary)
        editor_layout.addLayout(reference_toolbar)

        self.reference_list = QListWidget()
        self.reference_list.setObjectName("referenceList")
        self.reference_list.setViewMode(QListWidget.IconMode)
        self.reference_list.setIconSize(QSize(112, 112))
        self.reference_list.setGridSize(QSize(132, 146))
        self.reference_list.setResizeMode(QListWidget.Adjust)
        self.reference_list.setMovement(QListWidget.Static)
        self.reference_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.reference_list.setToolTip("Encrypted reference images for the selected character")
        editor_layout.addWidget(self.reference_list, 1)
        splitter.addWidget(editor)
        splitter.setSizes([310, 900])

        self._update_engine_status()
        self.refresh()

    @staticmethod
    def _button(text: str, tooltip: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.setToolTip(tooltip)
        button.clicked.connect(slot)
        return button

    def set_video_context(self, project_id: str, video_name: str, selected_character_id: str = "") -> None:
        self.project_id = project_id
        self.video_name = video_name
        self.refresh(selected_character_id)

    def clear_video_context(self) -> None:
        self.project_id = ""
        self.video_name = ""
        self.refresh()

    def set_match_threshold(self, threshold: float) -> None:
        self.match_threshold = min(0.95, max(0.05, float(threshold)))

    def refresh(self, select_id: str = "") -> None:
        if self.current_character and self.current_character.is_adhoc:
            self.current_character = None
        current_id = select_id or (self.current_character.id if self.current_character else "")
        self.character_list.blockSignals(True)
        self.character_list.clear()
        if self.store:
            for character in self.store.characters():
                count = len(self.store.references(character.id))
                item = QListWidgetItem(f"{character.name}  ·  {count} photo{'s' if count != 1 else ''}")
                item.setData(CHARACTER_ROLE, character)
                self.character_list.addItem(item)
                if character.id == current_id:
                    self.character_list.setCurrentItem(item)
        self.character_list.blockSignals(False)
        selected_item = self.character_list.currentItem()
        if selected_item:
            self._select_character(selected_item.data(CHARACTER_ROLE))
        enabled = bool(self.store)
        self.new_button.setEnabled(enabled)
        self.batch_button.setEnabled(enabled)
        if not self.current_character and self.character_list.count():
            self.character_list.setCurrentRow(0)
        self._update_editor_enabled()

    def selected_character(self) -> CharacterRecord | None:
        return self.current_character

    def reference_thumbnails(self, limit: int = 4) -> list[QPixmap]:
        if not self.store or not self.current_character:
            return []
        pixmaps: list[QPixmap] = []
        for reference in self.store.references(self.current_character.id)[:limit]:
            pixmap = QPixmap()
            if pixmap.loadFromData(self.store.reference_bytes(reference.id)):
                pixmaps.append(pixmap)
        return pixmaps

    def _persistent_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current:
            self._select_character(current.data(CHARACTER_ROLE))

    def _select_character(self, character: CharacterRecord | None) -> None:
        self.current_character = character
        self.reference_list.clear()
        if not character or not self.store:
            self.selection_title.setText("Select a person")
            self._update_editor_enabled()
            return
        self.selection_title.setText(character.name)
        for reference in self.store.references(character.id):
            pixmap = QPixmap()
            pixmap.loadFromData(self.store.reference_bytes(reference.id))
            icon = QIcon(pixmap) if not pixmap.isNull() else QIcon()
            item = QListWidgetItem(icon, reference.filename)
            item.setData(REFERENCE_ROLE, reference)
            item.setToolTip(f"Encrypted reference: {reference.filename}")
            self.reference_list.addItem(item)
        count = self.reference_list.count()
        self.validation_summary.setText(f"{count} photo{'s' if count != 1 else ''} · 5–15 varied photos work best.")
        self._update_editor_enabled()

    def _update_editor_enabled(self) -> None:
        selected = bool(self.current_character and self.store)
        persistent = selected
        self.rename_action.setEnabled(bool(persistent))
        self.delete_action.setEnabled(bool(persistent))
        self.rename_button.setEnabled(bool(persistent))
        self.export_button.setEnabled(bool(persistent))
        self.delete_button.setEnabled(bool(persistent))
        self.add_references_button.setEnabled(selected)
        self.add_photos_action.setEnabled(selected)
        self.add_folder_action.setEnabled(selected)
        self.capture_action.setEnabled(bool(selected and self.project_id and self.frame_provider))
        self.delete_reference_button.setEnabled(selected)
        self.validate_button.setEnabled(selected)
        self.test_button.setEnabled(selected)

    def _write_photos(self, character: CharacterRecord, folder: Path) -> int:
        folder.mkdir(parents=True, exist_ok=True)
        written = 0
        for reference in self.store.references(character.id):
            name = Path(reference.filename).name or f"photo-{written + 1:02d}.png"
            target = folder / name
            number = 2
            while target.exists():
                target = folder / f"{Path(name).stem} ({number}){Path(name).suffix}"
                number += 1
            target.write_bytes(self.store.reference_bytes(reference.id))
            written += 1
        return written

    def _export_current(self) -> None:
        if not self.store or not self.current_character:
            return
        folder = choose_directory(self, f"Export photos of {self.current_character.name} to", key="people_export")
        if not folder:
            return
        target = Path(folder) / _folder_name(self.current_character.name)
        count = self._write_photos(self.current_character, target)
        QMessageBox.information(self, "Photos exported", f"Saved {count} photo{'s' if count != 1 else ''} to:\n{target}")

    def _export_all(self) -> None:
        if not self.store:
            return
        people = self.store.characters()
        if not people:
            QMessageBox.information(self, "Nobody saved", "Save a person first.")
            return
        folder = choose_directory(self, "Export everyone's photos to", key="people_export")
        if not folder:
            return
        total = sum(self._write_photos(person, Path(folder) / _folder_name(person.name)) for person in people)
        QMessageBox.information(
            self, "Photos exported",
            f"Saved {total} photos for {len(people)} {'person' if len(people) == 1 else 'people'} to:\n{folder}\n\n"
            "Each person has their own folder, so Batch import can read it back.",
        )

    def _character_context_menu(self, position) -> None:
        item = self.character_list.itemAt(position)
        if not item:
            return
        self.character_list.setCurrentItem(item)
        menu = QMenu(self.character_list)
        menu.addAction(self.rename_action)
        menu.addAction(self.delete_action)
        menu.exec(self.character_list.viewport().mapToGlobal(position))

    def _new_character(self) -> None:
        if not self.store:
            return
        name, accepted = QInputDialog.getText(self, "New person", "Name:")
        if accepted and name.strip():
            try:
                character = self.store.create_character(name)
                self.refresh(character.id)
            except (ValueError, RuntimeError) as exc:
                QMessageBox.warning(self, "Could not create character", str(exc))

    def _rename_character(self) -> None:
        self._rename_current("Rename person")

    def _rename_current(self, title: str) -> None:
        if not self.store or not self.current_character:
            return
        name, accepted = QInputDialog.getText(self, title, "Character name:", text=self.current_character.name)
        if accepted and name.strip():
            try:
                self.store.rename_character(self.current_character.id, name)
                self.refresh(self.current_character.id)
            except (ValueError, RuntimeError) as exc:
                QMessageBox.warning(self, "Could not rename character", str(exc))

    def _delete_character(self) -> None:
        if not self.store or not self.current_character or self.current_character.is_adhoc:
            return
        count = len(self.store.references(self.current_character.id))
        answer = QMessageBox.question(
            self,
            "Delete person",
            f"Delete '{self.current_character.name}' and its {count} encrypted reference image(s)?",
        )
        if answer == QMessageBox.Yes:
            deleted_id = self.current_character.id
            self.store.delete_character(self.current_character.id)
            self.current_character = None
            self.refresh()
            self.characterDeleted.emit(deleted_id)

    def _add_photos(self) -> None:
        paths = [Path(path) for path in choose_images(self)]
        self._validate_and_add([(path.name, path.read_bytes()) for path in paths if path.is_file()])

    def _add_folder(self) -> None:
        folder = choose_directory(self, "Add reference folder to selected character", key="reference_folder")
        if not folder:
            return
        root = Path(folder)
        paths = [
            path for path in sorted(root.rglob("*"), key=lambda path: str(path).casefold())
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        ]
        self._validate_and_add([(str(path.relative_to(root)), path.read_bytes()) for path in paths])

    def _batch_import(self) -> None:
        if not self.store or not self.face_engine:
            return
        status = self.face_engine.status()
        if not status.available:
            QMessageBox.warning(self, "Face engine unavailable", status.detail)
            return
        folder = choose_directory(
            self,
            "Choose character-library root folder",
            key="character_library_root",
        )
        if not folder:
            return
        root = Path(folder)
        batches = discover_character_folders(root)
        if not batches:
            QMessageBox.information(
                self,
                "No character folders found",
                "The selected root needs one subfolder per character, with reference images inside.\n\n"
                "Example: Characters/Alex/*.jpg and Characters/Sam/*.png",
            )
            return
        image_count = sum(len(paths) for _name, paths in batches)
        if QMessageBox.question(
            self,
            "Batch import characters",
            f"Validate {image_count} image(s) across {len(batches)} character folder(s)?\n\n"
            "Immediate subfolder names become reusable characters. Existing names are merged safely.",
        ) != QMessageBox.Yes:
            return
        self._run_task(
            f"Validating {image_count} batch reference image(s)…",
            lambda cancelled: self._validate_character_batches(root, batches, cancelled),
            self._batch_import_validated,
        )

    def _validate_character_batches(
        self,
        root: Path,
        batches: list[tuple[str, list[Path]]],
        cancelled: Callable[[], bool],
    ) -> list[tuple[str, list[tuple[str, bytes]], list[ReferenceAssessment]]]:
        if not self.face_engine:
            return []
        output: list[tuple[str, list[tuple[str, bytes]], list[ReferenceAssessment]]] = []
        for character_name, paths in batches:
            if cancelled():
                break
            character_root = root / character_name
            named_bytes = [
                (str(path.relative_to(character_root)), path.read_bytes())
                for path in paths
                if not cancelled()
            ]
            results = self.face_engine.assess_references(named_bytes, cancelled)
            output.append((character_name, named_bytes, results))
        return output

    def _batch_import_validated(
        self,
        batches: list[tuple[str, list[tuple[str, bytes]], list[ReferenceAssessment]]],
    ) -> None:
        if not self.store:
            return
        warnings = [
            f"• {character}: {result.name} — {result.warning}"
            for character, _images, results in batches
            for result in results
            if result.valid and result.warning
        ]
        accept_warnings = True
        if warnings:
            shown = "\n".join(warnings[:12])
            remaining = len(warnings) - 12
            suffix = f"\n• …and {remaining} more" if remaining > 0 else ""
            accept_warnings = QMessageBox.question(
                self,
                "Batch reference warnings",
                f"Some usable images need review:\n\n{shown}{suffix}\n\nImport them anyway?",
            ) == QMessageBox.Yes

        existing = {item.name.casefold(): item for item in self.store.characters()}
        characters_created = 0
        characters_updated: set[str] = set()
        added = 0
        rejected = 0
        duplicates = 0
        last_character: CharacterRecord | None = None
        changed_characters: dict[str, CharacterRecord] = {}
        for character_name, named_bytes, results in batches:
            by_name = {name: data for name, data in named_bytes}
            usable = [
                result for result in results
                if result.valid and (accept_warnings or not result.warning)
            ]
            rejected += len(results) - len(usable)
            if not usable:
                continue
            character = existing.get(character_name.casefold())
            if not character:
                character = self.store.create_character(character_name)
                existing[character_name.casefold()] = character
                characters_created += 1
            else:
                characters_updated.add(character.id)
            last_character = character
            for result in usable:
                try:
                    self.store.add_reference(character.id, result.name, by_name[result.name])
                    added += 1
                    changed_characters[character.id] = character
                except ValueError:
                    duplicates += 1

        self.refresh(last_character.id if last_character else "")
        for character in changed_characters.values():
            self.referencesChanged.emit(character)
        QMessageBox.information(
            self,
            "Batch import complete",
            f"Created {characters_created} character(s); updated {len(characters_updated)}.\n"
            f"Added {added} encrypted reference(s); skipped {duplicates} duplicate(s) and {rejected} rejected image(s).",
        )

    def _capture_frame(self) -> None:
        if not self.frame_provider:
            return
        pixmap = self.frame_provider()
        if pixmap.isNull():
            QMessageBox.information(self, "No frame", "Open a video and seek to a clear face first.")
            return
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.WriteOnly)
        pixmap.save(buffer, "PNG")
        name = f"frame-{self.project_id[:8]}-{self.reference_list.count() + 1:02d}.png"
        self._validate_and_add([(name, bytes(data))])

    def _validate_and_add(self, named_bytes: list[tuple[str, bytes]]) -> None:
        if not named_bytes or not self.store or not self.current_character:
            return
        if not self.face_engine:
            QMessageBox.warning(self, "Face engine unavailable", "Clipivo's face engine is not configured.")
            return
        status = self.face_engine.status()
        if not status.available:
            QMessageBox.warning(self, "Face engine unavailable", status.detail)
            return
        self._run_task(
            "Validating reference faces…",
            lambda cancelled: self.face_engine.assess_references(named_bytes, cancelled),
            lambda results: self._references_validated(named_bytes, results),
        )

    def _references_validated(
        self,
        named_bytes: list[tuple[str, bytes]],
        results: list[ReferenceAssessment],
    ) -> None:
        if not self.store or not self.current_character:
            return
        by_name = {name: data for name, data in named_bytes}
        valid = [item for item in results if item.valid]
        warnings = [item for item in valid if item.warning]
        invalid = [item for item in results if not item.valid]
        if warnings:
            detail = "\n".join(f"• {item.name}: {item.warning}" for item in warnings)
            if QMessageBox.question(
                self,
                "Reference consistency warning",
                f"These references need review:\n\n{detail}\n\nAdd the usable images anyway?",
            ) != QMessageBox.Yes:
                valid = [item for item in valid if not item.warning]
        added = 0
        errors: list[str] = []
        for item in valid:
            try:
                self.store.add_reference(self.current_character.id, item.name, by_name[item.name])
                added += 1
            except (ValueError, RuntimeError) as exc:
                errors.append(f"{item.name}: {exc}")
        self.refresh(self.current_character.id)
        self.referencesChanged.emit(self.current_character)
        rejected = "\n".join(f"• {item.name}: {item.reason}" for item in invalid)
        extra = ("\n\nRejected:\n" + rejected) if rejected else ""
        if errors:
            extra += "\n\nNot added:\n" + "\n".join(f"• {line}" for line in errors)
        QMessageBox.information(self, "References processed", f"Added {added} validated reference image(s).{extra}")

    def _delete_reference(self) -> None:
        if not self.store or not self.current_character or not self.reference_list.currentItem():
            QMessageBox.information(self, "Select a reference", "Select a reference image to remove first.")
            return
        reference = self.reference_list.currentItem().data(REFERENCE_ROLE)
        if QMessageBox.question(self, "Remove reference", f"Remove '{reference.filename}' from this character?") == QMessageBox.Yes:
            self.store.delete_reference(self.current_character.id, reference.id)
            self.refresh(self.current_character.id)
            self.referencesChanged.emit(self.current_character)

    def _validate_set(self) -> None:
        if not self.store or not self.current_character or not self.face_engine:
            return
        references = list(self.store.iter_reference_bytes(self.current_character.id))
        if not references:
            QMessageBox.information(self, "No references", "Add at least one reference image first.")
            return
        self._run_task(
            "Validating reference set…",
            lambda cancelled: self.face_engine.assess_references(references, cancelled),
            self._set_validated,
        )

    def _set_validated(self, results: list[ReferenceAssessment]) -> None:
        valid = [item for item in results if item.valid]
        problems = [item for item in results if not item.valid or item.warning]
        self.validation_summary.setText(f"{len(valid)}/{len(results)} usable · {len(problems)} warning(s)")
        detail = "\n".join(f"• {item.name}: {item.reason or item.warning}" for item in problems)
        QMessageBox.information(
            self,
            "Reference validation",
            f"{len(valid)} of {len(results)} references contain a usable face."
            + (f"\n\nReview:\n{detail}" if detail else "\n\nThe set is consistent."),
        )

    def _test_photo(self) -> None:
        if not self.store or not self.current_character or not self.face_engine:
            return
        paths = choose_images(self, "Choose a photo to test", key="test_photo")
        if not paths:
            return
        data = Path(paths[0]).read_bytes()
        references = list(self.store.iter_reference_bytes(self.current_character.id))
        self._run_task(
            "Matching test photo…",
            lambda _cancelled: self.face_engine.match_image(data, references, self.match_threshold),
            self._photo_matched,
        )

    def _photo_matched(self, result: MatchAssessment) -> None:
        verdict = "MATCH" if result.matched else "NO MATCH"
        QMessageBox.information(
            self,
            "Still-image match test",
            f"{verdict}\n\nBest similarity: {result.score:.3f}\n"
            f"Threshold: {self.match_threshold:.3f}\nFaces detected: {result.faces_detected}",
        )

    def _run_task(
        self,
        label: str,
        task: Callable[[Callable[[], bool]], object],
        completed: Callable[[object], None],
    ) -> None:
        if self._worker and self._worker.isRunning():
            return
        self._progress = QProgressDialog(label, "Cancel", 0, 0, self)
        self._progress.setWindowTitle("Clipivo face engine")
        self._progress.setMinimumDuration(0)
        self._progress.setToolTip("Clipivo is running local face recognition")
        worker = FaceTaskWorker(task, self)
        self._worker = worker
        worker.succeeded.connect(lambda result: self._task_succeeded(result, completed))
        worker.failed.connect(self._task_failed)
        worker.cancelled.connect(self._task_cancelled)
        self._progress.canceled.connect(worker.cancel)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _task_succeeded(self, result: object, completed: Callable[[object], None]) -> None:
        if self._progress:
            self._progress.close()
        self._worker = None
        completed(result)
        self._update_engine_status()

    def _task_failed(self, detail: str) -> None:
        if self._progress:
            self._progress.close()
        self._worker = None
        QMessageBox.critical(self, "Face-engine error", detail)
        self._update_engine_status()

    def _task_cancelled(self) -> None:
        if self._progress:
            self._progress.close()
        self._worker = None
        self.validation_summary.setText("Reference task cancelled")

    def _update_engine_status(self) -> None:
        if not self.face_engine:
            self.engine_status.setText("Engine unavailable")
            return
        status = self.face_engine.status()
        self.engine_status.setText(f"{status.device} · {'Ready' if status.available else 'Setup required'}")
        self.engine_status.setToolTip(status.detail)

    def shutdown(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.wait(5000)


def _folder_name(name: str) -> str:
    cleaned = "".join("_" if character in '/\\:*?"<>|' or ord(character) < 32 else character for character in name).strip(" .")
    return cleaned or "Person"
