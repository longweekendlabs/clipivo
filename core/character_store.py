from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import uuid4


KEYRING_SERVICE = "clipivo"
KEYRING_KEY = "character-store-key"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


class CharacterStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class CharacterRecord:
    id: str
    name: str
    scope: str
    project_id: str = ""

    @property
    def is_adhoc(self) -> bool:
        return self.scope == "adhoc"


@dataclass(frozen=True)
class ReferenceRecord:
    id: str
    filename: str
    digest: str


def default_store_dir() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "clipivo" / "characters"


class CharacterStore:
    """Clipivo-owned encrypted reference store.

    Persistent characters are reusable. Ad-hoc characters are tied to one
    video project id and never appear in the reusable library.
    """

    def __init__(self, data_dir: Path | None = None, encryption_key: bytes | None = None):
        self.directory = Path(data_dir) if data_dir else default_store_dir()
        self.blobs = self.directory / "blobs"
        self.index_path = self.directory / "index.enc"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self._aesgcm = self._aes_class()
        self._key = encryption_key or self._load_or_create_key()
        if len(self._key) != 32:
            raise CharacterStoreError("Character-store encryption key must be 256 bits.")
        self._index = self._load_index()

    @staticmethod
    def _aes_class():
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError as exc:
            raise CharacterStoreError(
                "Encrypted character storage requires the 'cryptography' package."
            ) from exc
        return AESGCM

    def _load_or_create_key(self) -> bytes:
        try:
            import keyring

            encoded = keyring.get_password(KEYRING_SERVICE, KEYRING_KEY)
            if encoded:
                return base64.b64decode(encoded)
            key = self._aesgcm.generate_key(bit_length=256)
            keyring.set_password(KEYRING_SERVICE, KEYRING_KEY, base64.b64encode(key).decode("ascii"))
            return key
        except Exception as exc:
            raise CharacterStoreError(
                "Clipivo could not unlock its encrypted character store through the OS keyring. "
                f"Make sure KWallet or Secret Service is available. [{type(exc).__name__}: {exc}]"
            ) from exc

    def _encrypt(self, payload: bytes) -> bytes:
        nonce = os.urandom(12)
        return nonce + self._aesgcm(self._key).encrypt(nonce, payload, None)

    def _decrypt(self, payload: bytes) -> bytes:
        if len(payload) < 13:
            raise CharacterStoreError("Encrypted character data is truncated.")
        return self._aesgcm(self._key).decrypt(payload[:12], payload[12:], None)

    def _load_index(self) -> dict:
        if not self.index_path.exists():
            return {"version": 1, "characters": {}}
        try:
            payload = json.loads(self._decrypt(self.index_path.read_bytes()))
            if payload.get("version") != 1 or not isinstance(payload.get("characters"), dict):
                raise ValueError("unsupported index version")
            return payload
        except CharacterStoreError:
            raise
        except Exception as exc:
            raise CharacterStoreError(
                f"Clipivo could not decrypt its character index. [{type(exc).__name__}: {exc}]"
            ) from exc

    def _save_index(self) -> None:
        temporary = self.index_path.with_suffix(".tmp")
        temporary.write_bytes(self._encrypt(json.dumps(self._index, separators=(",", ":")).encode("utf-8")))
        temporary.replace(self.index_path)

    def characters(self, scope: str = "persistent", project_id: str = "") -> list[CharacterRecord]:
        records: list[CharacterRecord] = []
        for character_id, entry in self._index["characters"].items():
            if entry.get("scope", "persistent") != scope:
                continue
            if scope == "adhoc" and entry.get("project_id", "") != project_id:
                continue
            records.append(CharacterRecord(
                id=character_id,
                name=str(entry.get("name", "Unnamed")),
                scope=scope,
                project_id=str(entry.get("project_id", "")),
            ))
        return sorted(records, key=lambda item: item.name.casefold())

    def character(self, character_id: str) -> CharacterRecord | None:
        entry = self._index["characters"].get(character_id)
        if not entry:
            return None
        return CharacterRecord(
            id=character_id,
            name=str(entry.get("name", "Unnamed")),
            scope=str(entry.get("scope", "persistent")),
            project_id=str(entry.get("project_id", "")),
        )

    def adhoc_for_project(self, project_id: str) -> CharacterRecord | None:
        matches = self.characters("adhoc", project_id)
        return matches[0] if matches else None

    def create_character(self, name: str, *, scope: str = "persistent", project_id: str = "") -> CharacterRecord:
        name = name.strip()
        if not name:
            raise ValueError("Character name cannot be empty.")
        if scope not in {"persistent", "adhoc"}:
            raise ValueError("Character scope must be persistent or adhoc.")
        if scope == "adhoc" and not project_id:
            raise ValueError("An ad-hoc character must belong to a video project.")
        if scope == "persistent" and any(item.name.casefold() == name.casefold() for item in self.characters()):
            raise ValueError("A reusable character with that name already exists.")
        existing = self.adhoc_for_project(project_id) if scope == "adhoc" else None
        if existing:
            self.rename_character(existing.id, name)
            return self.character(existing.id)
        character_id = uuid4().hex
        self._index["characters"][character_id] = {
            "name": name,
            "scope": scope,
            "project_id": project_id if scope == "adhoc" else "",
            "references": [],
        }
        self._save_index()
        return self.character(character_id)

    def rename_character(self, character_id: str, name: str) -> None:
        name = name.strip()
        if not name:
            raise ValueError("Character name cannot be empty.")
        entry = self._require_character(character_id)
        if entry.get("scope", "persistent") == "persistent":
            duplicate = any(
                item.id != character_id and item.name.casefold() == name.casefold()
                for item in self.characters()
            )
            if duplicate:
                raise ValueError("A reusable character with that name already exists.")
        entry["name"] = name
        self._save_index()

    def delete_character(self, character_id: str) -> None:
        entry = self._index["characters"].pop(character_id, None)
        if not entry:
            return
        for reference in entry.get("references", []):
            self._blob_path(reference["id"]).unlink(missing_ok=True)
        self._save_index()

    def references(self, character_id: str) -> list[ReferenceRecord]:
        entry = self._require_character(character_id)
        return [ReferenceRecord(
            id=str(item["id"]),
            filename=str(item.get("filename", "reference")),
            digest=str(item.get("digest", "")),
        ) for item in entry.get("references", [])]

    def add_reference(self, character_id: str, filename: str, data: bytes) -> ReferenceRecord:
        if not data:
            raise ValueError("Reference image is empty.")
        entry = self._require_character(character_id)
        digest = sha256(data).hexdigest()
        for existing in entry.get("references", []):
            if existing.get("digest") == digest:
                raise ValueError("That reference image is already stored for this character.")
        reference_id = uuid4().hex
        temporary = self._blob_path(reference_id).with_suffix(".tmp")
        temporary.write_bytes(self._encrypt(data))
        temporary.replace(self._blob_path(reference_id))
        payload = {"id": reference_id, "filename": Path(filename).name, "digest": digest}
        entry.setdefault("references", []).append(payload)
        self._save_index()
        return ReferenceRecord(**payload)

    def add_reference_file(self, character_id: str, source: Path) -> ReferenceRecord:
        source = Path(source)
        if source.suffix.lower() not in IMAGE_EXTENSIONS:
            raise ValueError(f"Unsupported reference image type: {source.suffix or 'none'}")
        return self.add_reference(character_id, source.name, source.read_bytes())

    def delete_reference(self, character_id: str, reference_id: str) -> None:
        entry = self._require_character(character_id)
        entry["references"] = [item for item in entry.get("references", []) if item["id"] != reference_id]
        self._blob_path(reference_id).unlink(missing_ok=True)
        self._save_index()

    def reference_bytes(self, reference_id: str) -> bytes:
        try:
            return self._decrypt(self._blob_path(reference_id).read_bytes())
        except OSError as exc:
            raise CharacterStoreError(f"Reference blob is missing: {reference_id}") from exc

    def iter_reference_bytes(self, character_id: str):
        for reference in self.references(character_id):
            yield reference.filename, self.reference_bytes(reference.id)

    def fingerprint(self, character_id: str) -> str:
        record = self.character(character_id)
        if not record:
            return ""
        values = [record.id, record.name, record.scope]
        values.extend(reference.digest for reference in self.references(character_id))
        return sha256("\0".join(values).encode("utf-8")).hexdigest()

    def _require_character(self, character_id: str) -> dict:
        try:
            return self._index["characters"][character_id]
        except KeyError as exc:
            raise KeyError(f"Unknown character id: {character_id}") from exc

    def _blob_path(self, reference_id: str) -> Path:
        return self.blobs / f"{reference_id}.bin"

