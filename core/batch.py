from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import median
from queue import Empty, Full, Queue
from threading import Event, Thread
from typing import Callable
from uuid import uuid4

import av

from .face_engine import FaceEngine
from .models import ClipRange
from .framing import FRAMINGS, MAX_SIZES, find_static_band, split_shots
from .naming import NAMING_SCHEMES
from .timecode import format_duration
from .video_probe import probe_video


VIDEO_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}
QUEUE_STATES = {
    "queued", "analysing", "review", "exporting", "completed", "no_match",
    "cancelled", "failed",
}


SOURCE_AFTER = {
    "keep": "Leave it where it is",
    "subfolder": "Move into a “Clipivo done” folder beside it",
    "folder": "Move to a folder I choose",
    "trash": "Move to Trash",
}
DONE_SUBFOLDER = "Clipivo done"


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
    # Only these people are looked for in this video.
    person_ids: list[str] = field(default_factory=list)

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
            person_ids=[str(item) for item in payload.get("person_ids", [])],
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
    save_next_to_source: bool = True
    mode: str = "copy"
    source_after: str = "keep"
    source_folder: str = ""
    naming: str = "source_date"
    custom_name: str = ""
    # 100 copies the original; lower re-encodes smaller. Size limits the shorter side.
    output_quality: int = 100
    max_short_side: int = 0
    framing: str = "whole"

    def output_dir_for(self, source: Path) -> Path:
        if self.save_next_to_source or not self.output_dir:
            return Path(source).parent
        return Path(self.output_dir)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["version"] = 2
        return payload

    @classmethod
    def from_dict(cls, payload: dict) -> "BatchConfiguration":
        arrangement = str(payload.get("arrangement", "compilation"))
        quality = str(payload.get("quality", "balanced"))
        encoder = str(payload.get("video_encoder", "auto"))
        source_after = str(payload.get("source_after", "trash" if payload.get("trash_after_success") else "keep"))
        if source_after not in SOURCE_AFTER:
            source_after = "keep"
        naming = str(payload.get("naming", "source_date"))
        return cls(
            videos=[BatchVideoEntry.from_dict(item) for item in payload.get("videos", []) if isinstance(item, dict)],
            character_ids=[str(item) for item in payload.get("character_ids", [])],
            output_dir=str(payload.get("output_dir", "")),
            arrangement=arrangement if arrangement in {"separate", "compilation"} else "compilation",
            quality=quality if quality in {"rapid", "balanced", "thorough"} else "balanced",
            include_audio=bool(payload.get("include_audio", True)),
            video_encoder=encoder if encoder in {"auto", "nvenc", "cpu"} else "auto",
            save_next_to_source=bool(payload.get("save_next_to_source", not payload.get("output_dir"))),
            # Version 1 re-encoded by default; original quality is the default from version 2.
            mode="accurate" if payload.get("mode") == "accurate" and int(payload.get("version", 1)) >= 2 else "copy",
            source_after=source_after,
            source_folder=str(payload.get("source_folder", "")),
            naming=naming if naming in NAMING_SCHEMES else "source_date",
            custom_name=str(payload.get("custom_name", "")),
            output_quality=max(50, min(100, int(payload.get("output_quality", 100)))),
            max_short_side=int(payload.get("max_short_side", 0)) if int(payload.get("max_short_side", 0)) in MAX_SIZES else 0,
            framing=str(payload.get("framing", "whole")) if payload.get("framing") in FRAMINGS else "whole",
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
    box: tuple[float, float, float, float] | None = None
    frame_size: tuple[int, int] | None = None


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


class _SampledFrames(Thread):
    """Decode one frame per interval ahead of the consumer, bounded so memory stays small."""

    _DONE = object()

    def __init__(self, source: Path, interval_ms: int, skip_frame: str, stop: Event):
        super().__init__(daemon=True)
        self.source = source
        self.interval_ms = interval_ms
        self.skip_frame = skip_frame
        self.stop = stop
        self.queue: Queue = Queue(maxsize=6)
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            container = av.open(str(self.source))
            try:
                try:
                    stream = container.streams.video[0]
                except IndexError as exc:
                    raise RuntimeError("The selected file has no decodable video stream.") from exc
                stream.thread_type = "AUTO"
                stream.codec_context.skip_frame = self.skip_frame
                next_sample_ms = 0
                for frame in container.decode(stream):
                    if self.stop.is_set():
                        break
                    if frame.pts is None:
                        continue
                    timestamp_ms = max(0, round(float(frame.pts * stream.time_base) * 1000))
                    if timestamp_ms < next_sample_ms:
                        continue
                    while next_sample_ms <= timestamp_ms:
                        next_sample_ms += self.interval_ms
                    if not self._put((timestamp_ms, frame.to_ndarray(format="bgr24"))):
                        break
            finally:
                container.close()
        except BaseException as exc:  # noqa: BLE001 - re-raised in the consumer
            self.error = exc
        finally:
            self._put(self._DONE)

    def _put(self, item) -> bool:
        while not self.stop.is_set():
            try:
                self.queue.put(item, timeout=0.1)
                return True
            except Full:
                continue
        return False

    def __iter__(self):
        while True:
            try:
                item = self.queue.get(timeout=0.1)
            except Empty:
                if not self.is_alive() and self.queue.empty():
                    break
                continue
            if item is self._DONE:
                break
            yield item
        if self.error is not None:
            raise self.error


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
    refine_progress: Callable[[int, int], None] | None = None,
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
    last_progress_ms = -1
    stop = Event()
    # Decoding runs on the CPU while faces are matched on the GPU; overlapping
    # the two instead of alternating them is most of the scan's speed.
    frames = _SampledFrames(Path(source), interval_ms, skip_frame, stop)
    frames.start()
    try:
        for timestamp_ms, image in frames:
            if (cancelled and cancelled()) or (wait_if_paused and not wait_if_paused()):
                break
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
                    hits[target_id].append(AppearanceHit(
                        timestamp_ms, assessment.score, getattr(assessment, "box", None),
                        (int(image.shape[1]), int(image.shape[0])),
                    ))
            if progress and (last_progress_ms < 0 or timestamp_ms - last_progress_ms >= 500):
                progress(timestamp_ms, duration_ms, f"Scanning {format_duration(timestamp_ms)} / {format_duration(duration_ms)}")
                last_progress_ms = timestamp_ms
    finally:
        stop.set()
        frames.join()
    if progress:
        progress(duration_ms, duration_ms, "Scan complete")
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
            refine_progress,
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
    progress: Callable[[int, int], None] | None = None,
) -> list[ClipRange]:
    """Resolve coarse hits to nearby matching frames without adding hidden padding."""
    ranges: list[ClipRange] = []
    refinement_step_ms = 200
    for index, group in enumerate(groups):
        if cancelled and cancelled():
            break
        if progress:
            progress(index, len(groups))
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


TRACK_STEP_MS = 400
_CUT_DIFFERENCE = 24.0


def track_person_in_clips(
    source: Path,
    face_engine: FaceEngine,
    target_id: str,
    reference_matrix,
    threshold: float,
    ranges: list[ClipRange],
    *,
    cancelled: Callable[[], bool] | None = None,
    wait_if_paused: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> None:
    """Fill each clip's `shots` and `frame` for person framing.

    Every frame inside a clip is decoded to find camera cuts; the face is checked every
    TRACK_STEP_MS, and a small greyscale copy of each checked frame is kept to find static
    bottom graphics per shot.
    """
    import numpy as np

    total = max(1, sum(clip.duration_ms for clip in ranges))
    done = 0
    for clip in ranges:
        if cancelled and cancelled():
            return
        points: list[list[float]] = []
        cuts: list[int] = []
        smalls: list[tuple[int, object]] = []
        frame_size = (0, 0)
        previous = None
        recent: list[float] = []
        next_sample_ms = clip.start_ms
        container = av.open(str(Path(source)))
        try:
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"
            if clip.start_ms:
                container.seek(int((clip.start_ms / 1000) / float(stream.time_base)), stream=stream, backward=True, any_frame=False)
            for frame in container.decode(stream):
                if (cancelled and cancelled()) or (wait_if_paused and not wait_if_paused()):
                    return
                if frame.pts is None:
                    continue
                timestamp_ms = max(0, round(float(frame.pts * stream.time_base) * 1000))
                if timestamp_ms > clip.end_ms:
                    break
                # Bottom graphics stay put across cuts; leave them out of the cut test.
                tiny = frame.reformat(width=64, height=36, format="gray").to_ndarray().astype(np.float32)[:28]
                if timestamp_ms >= clip.start_ms and previous is not None:
                    difference = float(np.abs(tiny - previous).mean())
                    typical = median(recent) if recent else 0.0
                    if difference > _CUT_DIFFERENCE and difference > typical * 3 + 4:
                        cuts.append(timestamp_ms)
                    recent = (recent + [difference])[-12:]
                previous = tiny
                if timestamp_ms < clip.start_ms or timestamp_ms < next_sample_ms:
                    continue
                next_sample_ms = timestamp_ms + TRACK_STEP_MS
                image = frame.to_ndarray(format="bgr24")
                frame_size = (int(image.shape[1]), int(image.shape[0]))
                smalls.append((timestamp_ms, frame.reformat(width=160, height=90, format="gray").to_ndarray()))
                matcher = getattr(face_engine, "match_frame_many", None)
                if callable(matcher):
                    assessment = matcher(image, {target_id: (reference_matrix, threshold)})[target_id]
                else:
                    assessment = face_engine.match_frame(image, reference_matrix, threshold)
                box = getattr(assessment, "box", None)
                if assessment.matched and box:
                    points.append([timestamp_ms, (box[0] + box[2]) / 2, (box[1] + box[3]) / 2, box[3] - box[1]])
        finally:
            container.close()
        if frame_size[1]:
            height = frame_size[1]
            clip.frame = list(frame_size)
            clip.shots = split_shots(
                clip.start_ms, clip.end_ms, cuts, points,
                lambda start, end: find_static_band([small for t, small in smalls if start <= t < end], height),
            )
        done += clip.duration_ms
        if progress:
            progress(done, total)


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


def move_source_to_folder(source: Path, folder: Path) -> Path:
    """Move a finished source into `folder` without overwriting anything there."""
    source = Path(source)
    if not source.is_file():
        raise RuntimeError(f"Source is no longer available: {source}")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / source.name
    number = 2
    while target.exists():
        target = folder / f"{source.stem} ({number}){source.suffix}"
        number += 1
    shutil.move(str(source), str(target))
    return target


def handle_finished_source(source: Path, choice: str, chosen_folder: str = "") -> str:
    """Apply the user's choice to a source whose outputs all validated. Returns a short status."""
    if choice == "subfolder":
        move_source_to_folder(source, Path(source).parent / DONE_SUBFOLDER)
        return f"source moved to “{DONE_SUBFOLDER}”"
    if choice == "folder":
        if not chosen_folder:
            raise RuntimeError("Choose a folder for finished sources.")
        moved = move_source_to_folder(source, Path(chosen_folder))
        return f"source moved to {moved.parent}"
    if choice == "trash":
        move_source_to_trash(source)
        return "source moved to Trash"
    return ""


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
