from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable
from uuid import uuid4

import av

from .face_engine import FaceEngine
from .models import ClipRange
from .video_probe import probe_video


VIDEO_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}
QUEUE_STATES = {
    "queued", "analysing", "review", "exporting", "completed", "no_match",
    "cancelled", "failed",
}


@dataclass
class BatchVideoEntry:
    source: str
    enabled: bool = True
    manual_review: bool = False
    status: str = "queued"
    progress: int = 0
    detail: str = "Ready"
    outputs: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid4().hex)

    @property
    def path(self) -> Path:
        return Path(self.source)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "BatchVideoEntry":
        status = str(payload.get("status", "queued"))
        if status in {"analysing", "exporting"} or status not in QUEUE_STATES:
            status = "queued"
        return cls(
            id=str(payload.get("id") or uuid4().hex),
            source=str(payload["source"]),
            enabled=bool(payload.get("enabled", True)),
            manual_review=bool(payload.get("manual_review", False)),
            status=status,
            progress=max(0, min(100, int(payload.get("progress", 0)))),
            detail=str(payload.get("detail", "Ready")),
            outputs=[str(item) for item in payload.get("outputs", [])],
        )


@dataclass
class BatchConfiguration:
    videos: list[BatchVideoEntry] = field(default_factory=list)
    character_ids: list[str] = field(default_factory=list)
    output_dir: str = ""
    arrangement: str = "compilation"
    quality: str = "balanced"
    include_audio: bool = True
    video_encoder: str = "auto"
    trash_after_success: bool = False

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["version"] = 1
        return payload

    @classmethod
    def from_dict(cls, payload: dict) -> "BatchConfiguration":
        arrangement = str(payload.get("arrangement", "compilation"))
        quality = str(payload.get("quality", "balanced"))
        encoder = str(payload.get("video_encoder", "auto"))
        return cls(
            videos=[BatchVideoEntry.from_dict(item) for item in payload.get("videos", []) if isinstance(item, dict)],
            character_ids=[str(item) for item in payload.get("character_ids", [])],
            output_dir=str(payload.get("output_dir", "")),
            arrangement=arrangement if arrangement in {"separate", "compilation"} else "compilation",
            quality=quality if quality in {"rapid", "balanced", "thorough"} else "balanced",
            include_audio=bool(payload.get("include_audio", True)),
            video_encoder=encoder if encoder in {"auto", "nvenc", "cpu"} else "auto",
            trash_after_success=bool(payload.get("trash_after_success", False)),
        )


@dataclass(frozen=True)
class BatchCharacterTarget:
    id: str
    name: str
    references: list[tuple[str, bytes]]
    threshold: float


@dataclass(frozen=True)
class AppearanceHit:
    timestamp_ms: int
    score: float


@dataclass(frozen=True)
class CharacterAppearances:
    character_id: str
    character_name: str
    hits: tuple[AppearanceHit, ...]
    ranges: tuple[ClipRange, ...]


def default_batch_state_path() -> Path:
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return data_home / "clipivo" / "batch-queue.json"


def load_batch_configuration(path: Path | None = None) -> BatchConfiguration:
    target = Path(path) if path else default_batch_state_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        return BatchConfiguration.from_dict(payload) if isinstance(payload, dict) else BatchConfiguration()
    except (OSError, ValueError, TypeError, KeyError):
        return BatchConfiguration()


def save_batch_configuration(configuration: BatchConfiguration, path: Path | None = None) -> Path:
    target = Path(path) if path else default_batch_state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(configuration.to_dict(), indent=2), encoding="utf-8")
    temporary.replace(target)
    return target


def discover_videos(root: Path) -> list[Path]:
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted(
        (path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES),
        key=lambda path: str(path).casefold(),
    )


def safe_name_component(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).strip()
    normalized = re.sub(r"[\x00-\x1f/\\:*?\"<>|]+", "_", normalized)
    normalized = re.sub(r"\s+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("._")
    return normalized or "Character"


def analyse_video_for_characters(
    source: Path,
    face_engine: FaceEngine,
    targets: list[BatchCharacterTarget],
    *,
    duration_ms: int,
    quality: str = "balanced",
    cancelled: Callable[[], bool] | None = None,
    wait_if_paused: Callable[[], bool] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> list[CharacterAppearances]:
    if not targets:
        return []
    settings = {
        "rapid": (10_000, "NONKEY"),
        "balanced": (2_000, "NONREF"),
        "thorough": (750, "DEFAULT"),
    }
    interval_ms, skip_frame = settings.get(quality, settings["balanced"])
    matrices = {
        target.id: (face_engine.reference_matrix(target.references), target.threshold)
        for target in targets
    }
    hits: dict[str, list[AppearanceHit]] = {target.id: [] for target in targets}
    next_sample_ms = 0
    last_progress_ms = -1
    container = av.open(str(Path(source)))
    try:
        try:
            stream = container.streams.video[0]
        except IndexError as exc:
            raise RuntimeError("The selected file has no decodable video stream.") from exc
        stream.thread_type = "AUTO"
        stream.codec_context.skip_frame = skip_frame
        for frame in container.decode(stream):
            if cancelled and cancelled():
                break
            if wait_if_paused and not wait_if_paused():
                break
            if frame.pts is None:
                continue
            timestamp_ms = max(0, round(float(frame.pts * stream.time_base) * 1000))
            if timestamp_ms < next_sample_ms:
                continue
            while next_sample_ms <= timestamp_ms:
                next_sample_ms += interval_ms
            image = frame.to_ndarray(format="bgr24")
            matcher = getattr(face_engine, "match_frame_many", None)
            if callable(matcher):
                assessments = matcher(image, matrices)
            else:
                assessments = {
                    target_id: face_engine.match_frame(image, matrix, threshold)
                    for target_id, (matrix, threshold) in matrices.items()
                }
            for target_id, assessment in assessments.items():
                if assessment.matched:
                    hits[target_id].append(AppearanceHit(timestamp_ms, assessment.score))
            if progress and (last_progress_ms < 0 or timestamp_ms - last_progress_ms >= 500):
                progress(timestamp_ms, duration_ms, "Analysing faces")
                last_progress_ms = timestamp_ms
    finally:
        container.close()
    if progress:
        progress(duration_ms, duration_ms, "Analysis complete")
    results: list[CharacterAppearances] = []
    for target in targets:
        target_hits = hits[target.id]
        groups = _group_appearance_hits(target_hits, interval_ms)
        ranges = _refine_appearance_ranges(
            source,
            face_engine,
            target.id,
            matrices[target.id][0],
            target.threshold,
            groups,
            duration_ms,
            interval_ms,
            cancelled,
            wait_if_paused,
        )
        results.append(CharacterAppearances(
            target.id,
            target.name,
            tuple(target_hits),
            tuple(ranges),
        ))
    return results


def build_appearance_ranges(
    hits: list[AppearanceHit],
    duration_ms: int,
    sample_interval_ms: int,
    threshold: float,
    *,
    lead_in_ms: int = 0,
    lead_out_ms: int = 0,
) -> list[ClipRange]:
    if not hits:
        return []
    groups = _group_appearance_hits(hits, sample_interval_ms)
    ranges: list[ClipRange] = []
    for group in groups:
        start_ms = max(0, group[0].timestamp_ms - lead_in_ms)
        end_ms = min(duration_ms, group[-1].timestamp_ms + min(250, sample_interval_ms) + lead_out_ms)
        confidence = max(item.score for item in group)
        origin = "automatic" if confidence >= threshold + 0.08 else "automatic_uncertain"
        ranges.append(ClipRange(start_ms, max(start_ms + 1, end_ms), origin=origin))
    return _merge_ranges(ranges)


def _group_appearance_hits(
    hits: list[AppearanceHit],
    sample_interval_ms: int,
) -> list[list[AppearanceHit]]:
    if not hits:
        return []
    ordered = sorted(hits, key=lambda item: item.timestamp_ms)
    bridge_ms = max(1500, round(sample_interval_ms * 1.6))
    groups: list[list[AppearanceHit]] = [[ordered[0]]]
    for hit in ordered[1:]:
        if hit.timestamp_ms - groups[-1][-1].timestamp_ms <= bridge_ms:
            groups[-1].append(hit)
        else:
            groups.append([hit])
    return groups


def _refine_appearance_ranges(
    source: Path,
    face_engine: FaceEngine,
    target_id: str,
    reference_matrix,
    threshold: float,
    groups: list[list[AppearanceHit]],
    duration_ms: int,
    coarse_interval_ms: int,
    cancelled: Callable[[], bool] | None,
    wait_if_paused: Callable[[], bool] | None,
) -> list[ClipRange]:
    """Resolve coarse hits to nearby matching frames without adding hidden padding."""
    ranges: list[ClipRange] = []
    refinement_step_ms = 200
    for group in groups:
        if cancelled and cancelled():
            break
        first_hit = group[0].timestamp_ms
        last_hit = group[-1].timestamp_ms
        start_matches = _scan_refinement_window(
            source,
            face_engine,
            target_id,
            reference_matrix,
            threshold,
            max(0, first_hit - coarse_interval_ms),
            min(duration_ms, first_hit + refinement_step_ms),
            refinement_step_ms,
            cancelled,
            wait_if_paused,
        )
        end_matches = _scan_refinement_window(
            source,
            face_engine,
            target_id,
            reference_matrix,
            threshold,
            max(0, last_hit - refinement_step_ms),
            min(duration_ms, last_hit + coarse_interval_ms),
            refinement_step_ms,
            cancelled,
            wait_if_paused,
        )
        matched_times = [*start_matches, *end_matches]
        start_ms = min(matched_times) if matched_times else first_hit
        last_match_ms = max(matched_times) if matched_times else last_hit
        end_ms = min(duration_ms, last_match_ms + refinement_step_ms)
        confidence = max(item.score for item in group)
        origin = "automatic" if confidence >= threshold + 0.08 else "automatic_uncertain"
        ranges.append(ClipRange(start_ms, max(start_ms + 1, end_ms), origin=origin))
    return _merge_ranges(ranges)


def _scan_refinement_window(
    source: Path,
    face_engine: FaceEngine,
    target_id: str,
    reference_matrix,
    threshold: float,
    start_ms: int,
    end_ms: int,
    step_ms: int,
    cancelled: Callable[[], bool] | None,
    wait_if_paused: Callable[[], bool] | None,
) -> list[int]:
    if end_ms <= start_ms:
        return []
    matches: list[int] = []
    next_sample_ms = start_ms
    container = av.open(str(Path(source)))
    try:
        try:
            stream = container.streams.video[0]
        except IndexError as exc:
            raise RuntimeError("The selected file has no decodable video stream.") from exc
        stream.thread_type = "AUTO"
        if start_ms:
            container.seek(
                int((start_ms / 1000) / float(stream.time_base)),
                stream=stream,
                backward=True,
                any_frame=False,
            )
        for frame in container.decode(stream):
            if cancelled and cancelled():
                break
            if wait_if_paused and not wait_if_paused():
                break
            if frame.pts is None:
                continue
            timestamp_ms = max(0, round(float(frame.pts * stream.time_base) * 1000))
            if timestamp_ms < start_ms or timestamp_ms < next_sample_ms:
                continue
            if timestamp_ms > end_ms:
                break
            while next_sample_ms <= timestamp_ms:
                next_sample_ms += step_ms
            image = frame.to_ndarray(format="bgr24")
            matcher = getattr(face_engine, "match_frame_many", None)
            if callable(matcher):
                assessment = matcher(image, {target_id: (reference_matrix, threshold)})[target_id]
            else:
                assessment = face_engine.match_frame(image, reference_matrix, threshold)
            if assessment.matched:
                matches.append(timestamp_ms)
    finally:
        container.close()
    return matches


def _merge_ranges(ranges: list[ClipRange], *, join_gap_ms: int = 250) -> list[ClipRange]:
    merged: list[ClipRange] = []
    for current in sorted(ranges, key=lambda item: item.start_ms):
        if not merged or current.start_ms > merged[-1].end_ms + join_gap_ms:
            merged.append(current)
            continue
        previous = merged[-1]
        previous.end_ms = max(previous.end_ms, current.end_ms)
        if current.origin == "automatic_uncertain":
            previous.origin = "automatic_uncertain"
    return merged


def validate_export(path: Path, *, expected_duration_ms: int = 0) -> None:
    path = Path(path)
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"Export output is missing or empty: {path.name}")
    info = probe_video(path, include_keyframes=False)
    if info.duration_ms <= 0 or not any(stream.kind == "video" for stream in info.streams):
        raise RuntimeError(f"Export validation failed: {path.name}")
    if expected_duration_ms:
        tolerance_ms = max(1000, round(expected_duration_ms * 0.03))
        if abs(info.duration_ms - expected_duration_ms) > tolerance_ms:
            raise RuntimeError(
                f"Export duration is incomplete: expected {expected_duration_ms / 1000:.2f}s, "
                f"got {info.duration_ms / 1000:.2f}s ({path.name})"
            )
    tail_start_seconds = max(0.0, info.duration_ms / 1000 - 2.0)
    completed = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-xerror", "-ss", f"{tail_start_seconds:.3f}",
            "-i", str(path), "-map", "0:v:0", "-frames:v", "2", "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "tail decode failed"
        raise RuntimeError(f"Export is not playable through its ending: {detail}")


def move_source_to_trash(source: Path) -> None:
    source = Path(source)
    if not source.is_file():
        raise RuntimeError(f"Source is no longer available: {source}")
    gio = shutil.which("gio")
    if not gio:
        raise RuntimeError("Linux Trash integration requires the 'gio' command.")
    completed = subprocess.run(
        [gio, "trash", str(source.resolve())],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0 or source.exists():
        detail = completed.stderr.strip() or "The desktop Trash service rejected the source."
        raise RuntimeError(detail)
