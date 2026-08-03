from __future__ import annotations

import json
import os
from hashlib import sha256
from pathlib import Path

from .filters import VideoFilter
from .models import ClipRange


def state_path(video_path: Path) -> Path:
    resolved = video_path.resolve()
    try:
        stat = resolved.stat()
        identity = f"{resolved}\0{stat.st_size}\0{stat.st_mtime_ns}"
    except OSError:
        identity = str(resolved)
    digest = sha256(identity.encode("utf-8")).hexdigest()
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return data_home / "clipivo" / "projects" / f"{digest}.json"


def project_id(video_path: Path) -> str:
    return state_path(video_path).stem


def save_ranges(video_path: Path, ranges: list[ClipRange]) -> Path:
    payload = _load_payload(video_path)
    payload["clips"] = [item.to_dict() for item in ranges]
    return _save_payload(video_path, payload)


def save_filters(video_path: Path, filters: list[VideoFilter]) -> Path:
    payload = _load_payload(video_path)
    payload["filters"] = [item.to_dict() for item in filters]
    return _save_payload(video_path, payload)


def save_target_selection(video_path: Path, character_id: str, scope: str) -> Path:
    payload = _load_payload(video_path)
    payload["target"] = {"character_id": character_id, "scope": scope} if character_id else None
    return _save_payload(video_path, payload)


def save_find_result(
    video_path: Path,
    character_id: str,
    reference_fingerprint: str,
    threshold: float,
    timestamp_ms: int,
) -> Path:
    payload = _load_payload(video_path)
    results = [
        item for item in payload.get("find_results", [])
        if not (isinstance(item, dict) and item.get("character_id") == character_id)
    ]
    results.append({
        "character_id": character_id,
        "reference_fingerprint": reference_fingerprint,
        "threshold": round(float(threshold), 4),
        "timestamp_ms": max(0, int(timestamp_ms)),
        "search_version": "first-match-v1",
    })
    payload["find_results"] = results
    return _save_payload(video_path, payload)


def save_manual_appearances(video_path: Path, character_id: str, timestamps_ms: list[int]) -> Path:
    payload = _load_payload(video_path)
    existing = [
        item for item in payload.get("appearances", [])
        if not (
            isinstance(item, dict)
            and item.get("character_id") == character_id
            and item.get("source") == "manual"
        )
    ]
    existing.extend({
        "character_id": character_id,
        "timestamp_ms": max(0, int(timestamp)),
        "source": "manual",
    } for timestamp in sorted(set(timestamps_ms)))
    payload["appearances"] = existing
    return _save_payload(video_path, payload)


def _save_payload(video_path: Path, payload: dict) -> Path:
    target = state_path(video_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    payload["version"] = 5
    payload["source"] = str(video_path.resolve())
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(target)
    return target


def load_ranges(video_path: Path) -> list[ClipRange]:
    try:
        payload = _load_payload(video_path)
        return [ClipRange.from_dict(item) for item in payload.get("clips", [])]
    except (ValueError, KeyError, TypeError):
        return []


def load_filters(video_path: Path) -> list[VideoFilter]:
    try:
        payload = _load_payload(video_path)
        return [VideoFilter.from_dict(item) for item in payload.get("filters", [])]
    except (ValueError, KeyError, TypeError):
        return []


def load_target_selection(video_path: Path) -> dict:
    payload = _load_payload(video_path)
    target = payload.get("target")
    if not isinstance(target, dict):
        return {}
    character_id = str(target.get("character_id", ""))
    scope = str(target.get("scope", ""))
    return {"character_id": character_id, "scope": scope} if character_id and scope in {"persistent", "adhoc"} else {}


def load_find_result(
    video_path: Path,
    character_id: str,
    reference_fingerprint: str,
    threshold: float,
) -> int | None:
    for item in _load_payload(video_path).get("find_results", []):
        if not isinstance(item, dict):
            continue
        if (
            item.get("character_id") == character_id
            and item.get("reference_fingerprint") == reference_fingerprint
            and item.get("search_version") == "first-match-v1"
            and abs(float(item.get("threshold", -1)) - float(threshold)) < 0.0001
            and isinstance(item.get("timestamp_ms"), (int, float))
        ):
            return max(0, int(item["timestamp_ms"]))
    return None


def load_manual_appearances(video_path: Path, character_id: str) -> list[int]:
    payload = _load_payload(video_path)
    values = {
        max(0, int(item["timestamp_ms"]))
        for item in payload.get("appearances", [])
        if isinstance(item, dict)
        and item.get("character_id") == character_id
        and item.get("source") == "manual"
        and isinstance(item.get("timestamp_ms"), (int, float))
    }
    return sorted(values)


def _load_payload(video_path: Path) -> dict:
    target = state_path(video_path)
    if not target.is_file():
        return {"version": 5, "source": str(video_path.resolve()), "clips": [], "filters": [], "target": None, "appearances": [], "find_results": []}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"version": 5, "source": str(video_path.resolve()), "clips": [], "filters": [], "target": None, "appearances": [], "find_results": []}
    if payload.get("version") not in (1, 2, 3, 4, 5):
        return {"version": 5, "source": str(video_path.resolve()), "clips": [], "filters": [], "target": None, "appearances": [], "find_results": []}
    payload.setdefault("clips", [])
    payload.setdefault("filters", [])
    payload.setdefault("target", None)
    payload.setdefault("appearances", [])
    payload.setdefault("find_results", [])
    return payload
