from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, replace
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from threading import Event, Thread
from typing import Callable

from .filters import VideoFilter, active_filters, audio_filtergraph, video_filtergraph
from .framing import canvas_size, crop_filter, fit_filter, framed_shots, scale_filter
from .models import ClipRange
from .timecode import ffmpeg_time
from .video_probe import ProbeError, probe_video
from .downloads import tool_path


class ExportError(RuntimeError):
    pass


def _with_framed_size(shape: OutputShape, clips: list[ClipRange]) -> OutputShape:
    """One canvas for every framed shot, sized from the crops, capped by the size limit."""
    if shape.framing == "whole" or not all(shape.frame_size):
        return shape
    crops = [(*crop[:2], end - start) for clip in clips for start, end, crop in shape.runs(clip)]
    if not crops:
        return shape
    canvas, scale = canvas_size(crops, shape.max_short_side)
    return replace(shape, canvas=canvas, canvas_scale=scale)


def _same_panel(first: tuple, second: tuple, frame: tuple[int, int]) -> bool:
    """Two unmoving crops with the same left and right edges, within 1% of the picture."""
    (w1, _h1, keys1, _y1), (w2, _h2, keys2, _y2) = first, second
    if len(keys1) != 1 or len(keys2) != 1:
        return False
    tolerance = frame[0] * 0.01
    return abs(w1 - w2) <= tolerance and abs(keys1[0][1] - keys2[0][1]) <= tolerance


def framed_durations(shape: OutputShape, clips: list[ClipRange]) -> list[int]:
    """Length each clip will have once framing has left out shots without the person (or
    of the wrong shape); 0 for a clip that is left out entirely."""
    return [sum(piece.duration_ms for piece, _chain in shape.pieces(clip)) for clip in clips]


def detect_repeat_cycle(source: Path, clips: list[ClipRange], seconds: float = 3.0) -> int:
    """N when the source shows one repeated frame in every N, steadily, else 0.

    A few seconds are read from up to three clips. Converted broadcasts (50 fps published
    as 60 fps) repeat a frame in a fixed rhythm, which plays as a hitch several times a
    second; a frame that repeats now and then (a still picture) is not a rhythm.
    """
    import cv2
    import numpy as np
    import av

    gaps: list[int] = []
    for clip in sorted(clips, key=lambda item: -item.duration_ms)[:3]:
        middle = (clip.start_ms + clip.end_ms) / 2000
        try:
            container = av.open(str(source))
        except (OSError, av.error.FFmpegError):
            return 0
        try:
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"
            container.seek(int(middle / float(stream.time_base)), stream=stream, backward=True)
            previous, index, last_repeat = None, 0, None
            for frame in container.decode(stream):
                if frame.pts is None:
                    continue
                moment = float(frame.pts * stream.time_base)
                if moment < middle:
                    continue
                if moment > middle + seconds:
                    break
                small = cv2.resize(frame.to_ndarray(format="gray"), (160, 90), interpolation=cv2.INTER_AREA).astype(np.int16)
                if previous is not None and float(np.abs(small - previous).mean()) < 0.3:
                    if last_repeat is not None:
                        gaps.append(index - last_repeat)
                    last_repeat = index
                previous = small
                index += 1
        finally:
            container.close()
    if len(gaps) < 8:
        return 0
    cycle = max(set(gaps), key=gaps.count)
    return cycle if 3 <= cycle <= 12 and gaps.count(cycle) >= 0.85 * len(gaps) else 0


def cover_stream_index(source: Path) -> int | None:
    """Index of an embedded cover picture (a YouTube download's thumbnail), if the file has one."""
    try:
        completed = subprocess.run(
            [tool_path("ffprobe"), "-v", "error", "-select_streams", "v", "-show_entries",
             "stream=index:stream_disposition=attached_pic", "-of", "csv=p=0", str(source)],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in completed.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) >= 2 and parts[1] == "1":
            return int(parts[0])
    return None


def extract_cover(source: Path, max_width: int = 0) -> Path | None:
    """Save the embedded cover to Clipivo's cache and return its path, or None."""
    index = cover_stream_index(source)
    if index is None:
        return None
    stat = Path(source).stat()
    key = hashlib.sha1(f"{Path(source).resolve()}|{stat.st_size}|{int(stat.st_mtime)}|{max_width}".encode()).hexdigest()[:20]
    target = export_work_dir() / "covers" / f"{key}.png"
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [tool_path("ffmpeg"), "-nostdin", "-v", "error", "-y", "-i", str(source), "-map", f"0:{index}", "-frames:v", "1"]
    if max_width:
        command += ["-vf", f"scale='min({max_width},iw)':-2"]
    try:
        completed = subprocess.run([*command, str(target)], capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return target if completed.returncode == 0 and target.is_file() else None


def embed_cover(output: Path, cover: Path, logs: list[str] | None = None) -> bool:
    """Attach the source's cover to a finished file, so it shows the same thumbnail.
    Never fatal: the output is kept as it is if this fails."""
    temporary = output.with_name(f".{output.stem}.cover{output.suffix}")
    command = [
        tool_path("ffmpeg"), "-nostdin", "-v", "error", "-y", "-i", str(output), "-i", str(cover),
        "-map", "0", "-map", "1", "-c", "copy", "-disposition:v:1", "attached_pic",
        "-movflags", "+faststart", str(temporary),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=600, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        temporary.unlink(missing_ok=True)
        if logs is not None:
            logs.append(f"Cover not attached: {exc}")
        return False
    if completed.returncode != 0 or not temporary.is_file():
        temporary.unlink(missing_ok=True)
        if logs is not None:
            logs.append("Cover not attached: " + (completed.stderr.strip() or "ffmpeg failed"))
        return False
    temporary.replace(output)
    return True


def export_work_dir() -> Path:
    """Scratch space for segments: Clipivo's cache, never the user's video folders."""
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "clipivo"
    base.mkdir(parents=True, exist_ok=True)
    return base


def clean_stale_work(max_age_hours: float = 6.0) -> None:
    """Remove segment folders left by an export that was killed or crashed."""
    cutoff = time.time() - max_age_hours * 3600
    for folder in export_work_dir().glob("export-*"):
        try:
            if folder.is_dir() and folder.stat().st_mtime < cutoff:
                shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            continue


MIN_RUN_MS = 1000


@dataclass(frozen=True)
class OutputShape:
    """How re-encoded output looks: quality (50-100), size limit on the shorter side, framing."""

    quality: int = 95
    max_short_side: int = 0
    framing: str = "whole"
    frame_size: tuple[int, int] = (0, 0)
    # Size of framed output; chosen from the crops so a stitched video keeps one size.
    canvas: tuple[int, int] = (0, 0)
    canvas_scale: float = 1.0
    # The source repeats one frame in every this many (a 50 fps broadcast published at
    # 60 fps): re-encoded output drops the repeats so it plays smoothly. 0 = none.
    repeat_cycle: int = 0
    # The source's video codec and bitrate: re-encoded output never spends more on the
    # pixels it keeps than the source did, allowing for the codecs' efficiency.
    source_codec: str = ""
    source_kbps: int = 0

    def needs_reencode(self) -> bool:
        width, height = self.frame_size
        too_big = bool(self.max_short_side) and min(width, height) > self.max_short_side
        return self.quality < 100 or too_big or self.framing != "whole"

    def pieces(self, clip: ClipRange) -> list[tuple[ClipRange, tuple[str, str]]]:
        """Split a clip into exportable pieces with (before, after) user-filter chains.

        Whole picture is one piece. Following a person gives one piece per kept camera shot,
        each with its own crop, scaled and centred on one canvas. A clip can have no pieces.
        """
        if self.framing == "whole" or not all(self.frame_size):
            return [(clip, ("", scale_filter(self.max_short_side) if self.max_short_side else ""))]
        canvas = self.canvas
        pieces = []
        for start, end, (width, height, keys, y) in self.runs(clip):
            after = fit_filter(width, height, canvas if all(canvas) else (width, height), self.canvas_scale)
            pieces.append((ClipRange(start, end, origin=clip.origin), (crop_filter(width, height, keys, y), after)))
        return pieces

    def runs(self, clip: ClipRange) -> list[tuple[int, int, tuple]]:
        """(start, end, crop) for each stretch of a clip saved with one crop."""
        frame = tuple(clip.frame) if clip.frame else self.frame_size
        runs: list[list] = []
        for shot, (width, height, keys, y) in self.kept_shots(clip):
            start, end = max(clip.start_ms, int(shot["start"])), min(clip.end_ms, int(shot["end"]))
            if end - start < 40:
                continue
            last = runs[-1] if runs else None
            if last and last[1] == start and _same_panel(last[3], (width, height, keys, y), frame):
                # A cut elsewhere in the picture (another panel of a split screen) leaves this
                # person's panel where it was: keep one piece, cropped as its longest shot was,
                # since a short shot gives less to judge the panel's top and bottom by.
                if end - start > last[2]:
                    last[2], last[3] = end - start, (width, height, keys, y)
                last[1] = end
                continue
            runs.append([start, end, end - start, (width, height, keys, y)])
        # A stretch under a second with its own crop is a layout changing (an animated
        # transition), not a view of the person: leave it out.
        return [(start, end, crop) for start, end, _longest, crop in runs if end - start >= MIN_RUN_MS]

    def kept_shots(self, clip: ClipRange) -> list[tuple[dict, tuple]]:
        frame = tuple(clip.frame) if clip.frame else self.frame_size
        return framed_shots(clip.shots or [], self.framing, frame)


ProgressFn = Callable[[int, int, str], None]
FractionFn = Callable[[float], None]


def date_tag(now: datetime | None = None) -> str:
    """Return Clipivo's local YYMMDD_HHMM_SSS output tag."""
    value = now or datetime.now()
    return f"{value:%y%m%d_%H%M}_{value.second:03d}"


def export_ranges(
    source: Path,
    ranges: list[ClipRange],
    output_dir: Path,
    mode: str,
    stitched: bool,
    include_audio: bool = True,
    progress: ProgressFn | None = None,
    cancel_event: Event | None = None,
    target: Path | None = None,
    filters: list[VideoFilter] | None = None,
    source_fps: float = 25.0,
    video_encoder: str = "cpu",
    output_character: str = "",
    source_video_bitrate_kbps: int = 0,
    output_name: str | None = None,
    shape: OutputShape | None = None,
) -> list[Path]:
    """Export enabled ranges; `output_name` is the base file name (no extension).

    A stream copy can only start on a keyframe, so callers using copy mode
    should pass ranges through `snap_to_keyframes` first.
    """
    enabled = sorted((item for item in ranges if item.enabled), key=lambda item: item.start_ms)
    if not enabled:
        raise ExportError("No enabled clips are available to export.")
    output_dir.mkdir(parents=True, exist_ok=True)
    if target is not None:
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not stitched and len(enabled) != 1:
            raise ExportError("A named output file can only be used for one separate clip.")
    log_path = output_dir / "clipivo-export.log"
    logs: list[str] = []
    progress = progress or (lambda *_args: None)
    cancel_event = cancel_event or Event()
    filters = active_filters(filters)
    output_tag = date_tag()
    character_tail = f"_{_safe_output_component(output_character)}" if output_character else ""
    if filters or (shape and shape.needs_reencode()):
        mode = "accurate"
    try:
        resolved_encoder = _resolve_video_encoder(video_encoder) if mode == "accurate" else "cpu"
        if mode == "copy":
            encoder_label = "Stream copy"
        else:
            encoder_label = "NVIDIA NVENC" if resolved_encoder == "nvenc" else "CPU libx265"
        shape = _with_framed_size(shape or OutputShape(), enabled)
        if mode == "accurate":
            try:
                info = probe_video(source, include_keyframes=False)
                shape = replace(
                    shape, source_codec=info.video_codec,
                    source_kbps=source_video_bitrate_kbps or info.video_bitrate_kbps,
                    frame_size=shape.frame_size if all(shape.frame_size) else (info.width, info.height),
                )
            except ProbeError:
                pass
            shape = replace(shape, repeat_cycle=detect_repeat_cycle(source, enabled))
            if shape.repeat_cycle:
                logs.append(f"Source repeats one frame in every {shape.repeat_cycle}; dropping the repeats.")
        if shape.framing != "whole" and all(shape.frame_size):
            # Following a person leaves out shots without them, or of the wrong shape.
            enabled = [clip for clip in enabled if shape.pieces(clip)]
            if not enabled:
                wanted = "" if shape.framing == "dynamic" else f" in a {shape.framing} view"
                raise ExportError(f"The person was not found{wanted} in any clip, so nothing was saved.")
        if stitched:
            pieces = [piece for clip in enabled for piece in shape.pieces(clip)]
            result = [
                _export_stitched(
                    source, pieces, output_dir, mode, include_audio, progress,
                    cancel_event, logs, target, filters, source_fps,
                    resolved_encoder, video_encoder == "auto", encoder_label, output_tag,
                    character_tail, source_video_bitrate_kbps, output_name, shape,
                )
            ]
        else:
            result = []
            for index, clip in enumerate(enabled, start=1):
                _check_cancel(cancel_event)
                total_units = max(1, len(enabled) * 1000)
                clip_progress = lambda fraction, clip_index=index: progress(
                    round(((clip_index - 1) + fraction) * 1000),
                    total_units,
                    f"Exporting clip {clip_index} of {len(enabled)} · {encoder_label} · {round(fraction * 100)}%",
                )
                clip_progress(0.0)
                suffix = ".mp4"
                base = output_name or f"{source.stem}_{output_tag}{character_tail}"
                if len(enabled) == 1:
                    generated_name = f"{base}{suffix}"
                else:
                    generated_name = f"{base}_{index:03d}{suffix}"
                clip_target = target or _unique_path(output_dir / generated_name)
                pieces = shape.pieces(clip)
                if len(pieces) == 1:
                    piece, chain = pieces[0]
                    _export_one(
                        source, piece, clip_target, mode, include_audio, cancel_event,
                        logs, filters, source_fps, resolved_encoder,
                        video_encoder == "auto", clip_progress, source_video_bitrate_kbps, shape, chain,
                    )
                else:
                    _export_stitched(
                        source, pieces, output_dir, mode, include_audio,
                        lambda done, total, _text, report=clip_progress: report(done / max(1, total)),
                        cancel_event, logs, clip_target, filters, source_fps, resolved_encoder,
                        video_encoder == "auto", encoder_label, output_tag, character_tail,
                        source_video_bitrate_kbps, output_name, shape,
                    )
                result.append(clip_target)
            progress(total_units, total_units, "Export complete")
        cover = extract_cover(source)
        if cover:
            for output in result:
                embed_cover(output, cover, logs)
        return result
    except Exception as exc:
        log_path.write_text("\n\n".join(logs) or str(exc), encoding="utf-8")
        if isinstance(exc, ExportError):
            raise
        raise ExportError(str(exc)) from exc


def _export_stitched(
    source: Path,
    ranges: list[tuple[ClipRange, tuple[str, str]]],
    output_dir: Path,
    mode: str,
    include_audio: bool,
    progress: ProgressFn,
    cancel_event: Event,
    logs: list[str],
    target: Path | None,
    filters: list[VideoFilter],
    source_fps: float,
    video_encoder: str,
    allow_encoder_fallback: bool,
    encoder_label: str,
    output_tag: str,
    character_tail: str,
    source_video_bitrate_kbps: int,
    output_name: str | None = None,
    shape: OutputShape | None = None,
) -> Path:
    suffix = ".mp4"
    base = output_name or f"{source.stem}_{output_tag}{character_tail}"
    target = target or _unique_path(output_dir / f"{base}{suffix}")
    with tempfile.TemporaryDirectory(prefix="export-", dir=export_work_dir()) as temporary:
        temp_dir = Path(temporary)
        segments: list[Path] = []
        for index, (clip, chain) in enumerate(ranges, start=1):
            _check_cancel(cancel_event)
            total_units = (len(ranges) + 1) * 1000
            segment_progress = lambda fraction, segment_index=index: progress(
                round(((segment_index - 1) + fraction) * 1000),
                total_units,
                f"Preparing segment {segment_index} of {len(ranges)} · {encoder_label} · {round(fraction * 100)}%",
            )
            segment_progress(0.0)
            segment = temp_dir / f"segment-{index:04d}{suffix}"
            _export_one(
                source, clip, segment, mode, include_audio, cancel_event, logs,
                filters, source_fps, video_encoder, allow_encoder_fallback,
                segment_progress, source_video_bitrate_kbps, shape, chain,
            )
            segments.append(segment)

        concat_file = temp_dir / "segments.txt"
        concat_file.write_text(
            "\n".join(f"file '{_concat_escape(path.resolve())}'" for path in segments) + "\n",
            encoding="utf-8",
        )
        progress(len(ranges) * 1000, total_units, "Joining segments")
        command = [
            tool_path("ffmpeg"), "-hide_banner", "-y", "-fflags", "+genpts",
            "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-c", "copy", "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", str(target),
        ]
        try:
            _run(command, cancel_event, logs)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        progress(total_units, total_units, "Export complete")
    return target


def _export_one(
    source: Path,
    clip: ClipRange,
    target: Path,
    mode: str,
    include_audio: bool,
    cancel_event: Event,
    logs: list[str],
    filters: list[VideoFilter],
    source_fps: float,
    video_encoder: str,
    allow_encoder_fallback: bool,
    progress_fraction: FractionFn | None,
    source_video_bitrate_kbps: int = 0,
    shape: OutputShape | None = None,
    chain: tuple[str, str] = ("", ""),
) -> None:
    shape = shape or OutputShape()
    duration = clip.duration_ms
    if duration <= 0:
        raise ExportError("A clip has an invalid zero-length range.")
    if mode == "copy":
        # Seek 1 ms past the snapped keyframe so rounding never selects the previous one.
        command = [
            tool_path("ffmpeg"), "-hide_banner", "-y",
            "-ss", ffmpeg_time(clip.start_ms + 1), "-i", str(source),
            "-t", ffmpeg_time(duration),
            "-map", "0:v:0",
        ]
        if include_audio:
            command.extend(["-map", "0:a?"])
        command.extend(["-c", "copy", "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", str(target)])
    elif mode == "accurate":
        command = [
            tool_path("ffmpeg"), "-hide_banner", "-y",
            "-ss", ffmpeg_time(clip.start_ms), "-t", ffmpeg_time(duration),
            "-i", str(source),
            "-map", "0:v:0",
        ]
        if include_audio:
            command.extend(["-map", "0:a:0?"])
        before, after = chain
        steady = f"decimate=cycle={shape.repeat_cycle}" if shape.repeat_cycle else ""
        video_chain = ",".join(part for part in (before, steady, video_filtergraph(filters, source_fps), after) if part)
        if video_chain:
            command.extend(["-vf", video_chain])
        ceiling = _bitrate_ceiling(shape, before)
        command.extend(_video_encoder_arguments(
            "nvenc" if video_encoder == "nvenc" else "cpu", source_video_bitrate_kbps, shape.quality, ceiling,
        ))
        if include_audio:
            audio_chain = audio_filtergraph(filters, source_fps)
            if audio_chain:
                command.extend(["-af", audio_chain])
            command.extend(["-c:a", "aac", "-b:a", "192k"])
        command.extend(["-avoid_negative_ts", "make_zero", "-movflags", "+faststart", str(target)])
    else:
        raise ExportError(f"Unknown export mode: {mode}")
    try:
        try:
            _run(command, cancel_event, logs, duration, progress_fraction)
        except ExportError:
            if video_encoder != "nvenc" or not allow_encoder_fallback or cancel_event.is_set():
                raise
            logs.append("NVIDIA NVENC failed; retrying this clip with CPU libx265.")
            cpu_command = _replace_nvenc_with_cpu(command, source_video_bitrate_kbps, shape.quality, ceiling)
            _run(cpu_command, cancel_event, logs, duration, progress_fraction)
    except Exception:
        target.unlink(missing_ok=True)
        raise


def _run(
    command: list[str],
    cancel_event: Event,
    logs: list[str],
    duration_ms: int = 0,
    progress_fraction: FractionFn | None = None,
) -> None:
    command = [command[0], "-progress", "pipe:1", "-nostats", *command[1:]]
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError as exc:
        raise ExportError("FFmpeg was not found. Install FFmpeg to export clips.") from exc
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def drain_stdout() -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            stdout_lines.append(line)
            if progress_fraction and duration_ms > 0 and line.startswith("out_time_us="):
                try:
                    elapsed_us = int(line.split("=", 1)[1])
                except ValueError:
                    continue
                progress_fraction(min(1.0, max(0.0, elapsed_us / (duration_ms * 1000))))

    def drain_stderr() -> None:
        if process.stderr is None:
            return
        stderr_lines.extend(process.stderr)

    stdout_thread = Thread(target=drain_stdout, daemon=True)
    stderr_thread = Thread(target=drain_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    cancelled = False
    while process.poll() is None:
        if cancel_event.wait(0.1):
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
            cancelled = True
            break
    process.wait()
    stdout_thread.join(timeout=2)
    stderr_thread.join(timeout=2)
    if process.stdout is not None:
        process.stdout.close()
    if process.stderr is not None:
        process.stderr.close()
    stdout = "".join(stdout_lines)
    stderr = "".join(stderr_lines)
    logs.append("$ " + " ".join(command) + "\n" + stdout + stderr)
    if cancelled:
        raise ExportError("Export cancelled.")
    if process.returncode:
        detail = stderr.strip().splitlines()[-1] if stderr.strip() else "FFmpeg export failed."
        raise ExportError(detail)


@lru_cache(maxsize=1)
def nvenc_available() -> bool:
    try:
        completed = subprocess.run(
            [tool_path("ffmpeg"), "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and "hevc_nvenc" in completed.stdout


def _resolve_video_encoder(preference: str) -> str:
    if preference == "cpu":
        return "cpu"
    if preference == "nvenc":
        if not nvenc_available():
            raise ExportError("NVIDIA NVENC is not available in this FFmpeg installation.")
        return "nvenc"
    if preference == "auto":
        return "nvenc" if nvenc_available() else "cpu"
    raise ExportError(f"Unknown video encoder preference: {preference}")


def _replace_nvenc_with_cpu(
    command: list[str], source_video_bitrate_kbps: int = 0, quality: int = 95, ceiling: int = 0,
) -> list[str]:
    start = command.index("-c:v")
    end = start + 2
    while end < len(command) and command[end] not in {"-c:a", "-af", "-movflags"}:
        end += 1
    return [
        *command[:start],
        *_video_encoder_arguments("cpu", source_video_bitrate_kbps, quality, ceiling),
        *command[end:],
    ]


# Bits a codec needs for the same picture quality, relative to H.264.
_CODEC_COST = {"h264": 1.0, "hevc": 0.6, "vp9": 0.6, "av1": 0.45, "mpeg4": 1.4, "mpeg2video": 2.0}
_OUTPUT_COST = _CODEC_COST["hevc"]
# Room above the source's share for detail that gathers where the person is.
_CEILING_HEADROOM = 1.5


def _bitrate_ceiling(shape: OutputShape, crop_chain: str) -> int:
    """Most kbit/s a re-encoded piece may use: the source's bitrate for the share of the
    picture kept (after any Size shrinking), adjusted from the source's codec to HEVC.
    0 when the source's bitrate is unknown."""
    width, height = shape.frame_size
    if not shape.source_kbps or not width or not height:
        return 0
    kept_w, kept_h = width, height
    match = re.match(r"crop=(\d+):(\d+)", crop_chain or "")
    if match:
        kept_w, kept_h = int(match.group(1)), int(match.group(2))
    scale = shape.canvas_scale if match else 1.0
    if not match and shape.max_short_side and min(width, height) > shape.max_short_side:
        scale = shape.max_short_side / min(width, height)
    share = kept_w * kept_h * scale * scale / (width * height)
    cost = _OUTPUT_COST / _CODEC_COST.get(shape.source_codec, 1.0)
    return max(150, round(shape.source_kbps * share * cost * _CEILING_HEADROOM))


def _video_encoder_arguments(
    encoder: str, source_video_bitrate_kbps: int = 0, quality: int = 95, ceiling: int = 0,
) -> list[str]:
    """HEVC for re-encoded output: about a third smaller than H.264 at the same quality.
    Quality 95 (and 100 when re-encoding is unavoidable) is visually the source; each 5
    points lower trades detail for a smaller file.

    `ceiling` (kbit/s) keeps the file from outgrowing the source; without one, a loose
    ceiling only stops a very compact source, such as AV1, from ballooning.
    """
    quality = max(50, min(95, int(quality)))
    crf = 16 + (95 - quality) * 0.4  # 95 -> 16, 90 -> 18, 80 -> 22, 70 -> 26, 50 -> 34
    source_rate = max(0, int(source_video_bitrate_kbps))
    if not ceiling:
        ceiling = max(8_000, source_rate * 4) if source_rate else 0
    if encoder == "nvenc":
        arguments = [
            "-c:v", "hevc_nvenc", "-preset", "p7", "-tune", "hq", "-profile:v", "main",
            "-rc", "vbr", "-cq", f"{round(crf) + 4}", "-b:v", "0", "-pix_fmt", "yuv420p",
        ]
    else:
        arguments = [
            "-c:v", "libx265", "-preset", "medium", "-crf", f"{round(crf) + 2}", "-pix_fmt", "yuv420p",
            "-x265-params", "log-level=error",
        ]
    arguments.extend(["-tag:v", "hvc1"])
    if ceiling:
        arguments.extend(["-maxrate", f"{ceiling}k", "-bufsize", f"{ceiling * 2}k"])
    return arguments


def keyframe_at_or_before(source: Path, milliseconds: int) -> int:
    """Timestamp of the last keyframe at or before `milliseconds`, read from a small window.

    Packet flags, not decoded frames: ffprobe 4.x leaves a frame's timestamp empty when
    frames are skipped with -skip_frame, and packets need no decoding at all.
    """
    window_start = max(0.0, milliseconds / 1000 - 20)
    completed = subprocess.run(
        [
            tool_path("ffprobe"), "-v", "error", "-select_streams", "v:0",
            "-read_intervals", f"{window_start:.3f}%{milliseconds / 1000 + 0.05:.3f}",
            "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", str(source),
        ],
        capture_output=True, text=True, timeout=60, check=False,
    )
    best = 0
    for line in completed.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2 or "K" not in parts[1]:
            continue
        try:
            value = round(float(parts[0]) * 1000)
        except ValueError:
            continue
        if value <= milliseconds:
            best = max(best, value)
    return best


def snap_to_keyframes(source: Path, ranges: list[ClipRange]) -> list[ClipRange]:
    """Move each start back to its keyframe and merge clips that now overlap, so no footage repeats."""
    snapped: list[ClipRange] = []
    for item in sorted((clip for clip in ranges if clip.enabled), key=lambda clip: clip.start_ms):
        clip = ClipRange.from_dict(item.to_dict())
        clip.start_ms = keyframe_at_or_before(Path(source), clip.start_ms)
        if snapped and clip.start_ms <= snapped[-1].end_ms:
            snapped[-1].end_ms = max(snapped[-1].end_ms, clip.end_ms)
            continue
        snapped.append(clip)
    return snapped


def _check_cancel(event: Event) -> None:
    if event.is_set():
        raise ExportError("Export cancelled.")


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for number in range(2, 10_000):
        candidate = path.with_name(f"{path.stem}-{number}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise ExportError(f"Could not find a free output name for {path.name}.")


def _concat_escape(path: Path) -> str:
    return str(path).replace("'", "'\\''")


def _safe_output_component(value: str) -> str:
    cleaned = "".join("_" if character in '/\\:*?\"<>|' or ord(character) < 32 else character for character in value)
    cleaned = "_".join(cleaned.split())
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("._") or "Character"
