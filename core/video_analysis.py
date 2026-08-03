from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import av

from .face_engine import FaceEngine


@dataclass(frozen=True)
class AppearanceMatch:
    timestamp_ms: int
    score: float
    faces_detected: int
    samples_checked: int


def find_first_appearance(
    source: Path,
    face_engine: FaceEngine,
    references: list[tuple[str, bytes]],
    threshold: float,
    *,
    duration_ms: int = 0,
    start_ms: int = 0,
    end_ms: int | None = None,
    sample_interval_ms: int = 2000,
    refine_interval_ms: int = 250,
    keyframes_only: bool = False,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> AppearanceMatch | None:
    """Return the first sampled frame matching the selected character.

    Frames are decoded once, in source order. Face recognition is bounded by
    ``sample_interval_ms`` so a long compressed video is never random-seeked or
    analysed on every source frame.
    """
    reference_matrix = face_engine.reference_matrix(references)
    coarse_interval = max(250, int(sample_interval_ms))
    search_start = max(0, int(start_ms))
    search_end = max(search_start, int(end_ms if end_ms is not None else duration_ms))
    coarse = _scan_range(
        source,
        face_engine,
        reference_matrix,
        threshold,
        search_start,
        search_end,
        coarse_interval,
        keyframes_only,
        cancelled,
        progress,
    )
    if coarse is None or (cancelled and cancelled()) or refine_interval_ms <= 0:
        return coarse

    refined = _scan_range(
        source,
        face_engine,
        reference_matrix,
        threshold,
        max(search_start, coarse.timestamp_ms - coarse_interval),
        coarse.timestamp_ms,
        max(100, int(refine_interval_ms)),
        False,
        cancelled,
        None,
    )
    if refined is None:
        return coarse
    return AppearanceMatch(
        refined.timestamp_ms,
        refined.score,
        refined.faces_detected,
        coarse.samples_checked + refined.samples_checked,
    )


def find_end_of_appearance(
    source: Path,
    face_engine: FaceEngine,
    references: list[tuple[str, bytes]],
    threshold: float,
    *,
    start_ms: int,
    end_ms: int,
    sample_interval_ms: int = 750,
    absence_confirm_ms: int = 1500,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> int | None:
    """Return the first timestamp after a confirmed absence from an appearance.

    A single missed face is not enough to split an appearance. The character
    must remain unmatched for ``absence_confirm_ms`` before a later match is
    considered a distinct re-entry.
    """
    reference_matrix = face_engine.reference_matrix(references)
    interval_ms = max(250, int(sample_interval_ms))
    required_gap_ms = max(interval_ms, int(absence_confirm_ms))
    search_start = max(0, int(start_ms))
    search_end = max(search_start, int(end_ms))
    absent_since: int | None = None
    next_sample_ms = search_start
    container = av.open(str(Path(source)))
    try:
        try:
            stream = container.streams.video[0]
        except IndexError as exc:
            raise RuntimeError("The selected file has no decodable video stream.") from exc
        stream.thread_type = "AUTO"
        if search_start:
            container.seek(
                int((search_start / 1000) / float(stream.time_base)),
                stream=stream,
                backward=True,
                any_frame=False,
            )
        for frame in container.decode(stream):
            if cancelled and cancelled():
                return None
            if frame.pts is None:
                continue
            timestamp_ms = max(0, round(float(frame.pts * stream.time_base) * 1000))
            if timestamp_ms < search_start or timestamp_ms < next_sample_ms:
                continue
            if timestamp_ms > search_end:
                break
            while next_sample_ms <= timestamp_ms:
                next_sample_ms += interval_ms
            assessment = face_engine.match_frame(
                frame.to_ndarray(format="bgr24"),
                reference_matrix,
                threshold,
            )
            if assessment.matched:
                absent_since = None
            elif absent_since is None:
                absent_since = timestamp_ms
            elif timestamp_ms - absent_since >= required_gap_ms:
                if progress:
                    progress(timestamp_ms, search_end)
                return timestamp_ms
            if progress:
                progress(timestamp_ms, search_end)
    finally:
        container.close()
    if progress:
        progress(search_end, search_end)
    return None


def _scan_range(
    source: Path,
    face_engine: FaceEngine,
    reference_matrix,
    threshold: float,
    start_ms: int,
    end_ms: int,
    interval_ms: int,
    keyframes_only: bool,
    cancelled: Callable[[], bool] | None,
    progress: Callable[[int, int], None] | None,
) -> AppearanceMatch | None:
    checked = 0
    next_sample_ms = start_ms
    last_progress_ms = -1
    container = av.open(str(Path(source)))
    try:
        try:
            stream = container.streams.video[0]
        except IndexError as exc:
            raise RuntimeError("The selected file has no decodable video stream.") from exc
        stream.thread_type = "AUTO"
        if keyframes_only:
            stream.codec_context.skip_frame = "NONKEY"
        if start_ms:
            container.seek(
                int((start_ms / 1000) / float(stream.time_base)),
                stream=stream,
                backward=True,
                any_frame=False,
            )

        for frame in container.decode(stream):
            if cancelled and cancelled():
                return None
            if frame.pts is None:
                continue
            timestamp_ms = max(0, round(float(frame.pts * stream.time_base) * 1000))
            if timestamp_ms < start_ms or timestamp_ms < next_sample_ms:
                continue
            if timestamp_ms > end_ms:
                break
            while next_sample_ms <= timestamp_ms:
                next_sample_ms += interval_ms

            checked += 1
            assessment = face_engine.match_frame(
                frame.to_ndarray(format="bgr24"),
                reference_matrix,
                threshold,
            )
            if assessment.matched:
                if progress:
                    progress(timestamp_ms, end_ms)
                return AppearanceMatch(timestamp_ms, assessment.score, assessment.faces_detected, checked)
            if progress and (last_progress_ms < 0 or timestamp_ms - last_progress_ms >= 500):
                progress(timestamp_ms, end_ms)
                last_progress_ms = timestamp_ms
    finally:
        container.close()
    if progress:
        progress(end_ms, end_ms)
    return None
