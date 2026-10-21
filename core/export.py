from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from threading import Event, Thread
from typing import Callable

from .filters import VideoFilter, active_filters, audio_filtergraph, video_filtergraph
from .framing import crop_box, output_size, scale_filter
from .models import ClipRange
from .timecode import ffmpeg_time


class ExportError(RuntimeError):
    pass


@dataclass(frozen=True)
class OutputShape:
    """How re-encoded output looks: quality (50-100), size limit on the shorter side, framing."""

    quality: int = 95
    max_short_side: int = 0
    framing: str = "whole"
    frame_size: tuple[int, int] = (0, 0)

    def needs_reencode(self) -> bool:
        width, height = self.frame_size
        too_big = bool(self.max_short_side) and min(width, height) > self.max_short_side
        return self.quality < 100 or too_big or self.framing != "whole"

    def chain_for(self, clip: ClipRange) -> tuple[str, str]:
        """(before user filters, after user filters) for one clip."""
        if self.framing != "whole" and all(self.frame_size):
            x, y, w, h = crop_box(clip.subject, self.framing, self.frame_size)
            out_w, out_h = output_size(self.framing, self.max_short_side)
            return f"crop={w}:{h}:{x}:{y}", f"scale={out_w}:{out_h}:flags=lanczos,setsar=1"
        if self.max_short_side:
            return "", scale_filter(self.max_short_side)
        return "", ""


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
            encoder_label = "NVIDIA NVENC" if resolved_encoder == "nvenc" else "CPU libx264"
        if stitched:
            result = [
                _export_stitched(
                    source, enabled, output_dir, mode, include_audio, progress,
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
                _export_one(
                    source, clip, clip_target, mode, include_audio, cancel_event,
                    logs, filters, source_fps, resolved_encoder,
                    video_encoder == "auto", clip_progress, source_video_bitrate_kbps, shape,
                )
                result.append(clip_target)
            progress(total_units, total_units, "Export complete")
        return result
    except Exception as exc:
        log_path.write_text("\n\n".join(logs) or str(exc), encoding="utf-8")
        if isinstance(exc, ExportError):
            raise
        raise ExportError(str(exc)) from exc


def _export_stitched(
    source: Path,
    ranges: list[ClipRange],
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
    with tempfile.TemporaryDirectory(prefix=".clipivo-export-", dir=output_dir) as temporary:
        temp_dir = Path(temporary)
        segments: list[Path] = []
        for index, clip in enumerate(ranges, start=1):
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
                segment_progress, source_video_bitrate_kbps, shape,
            )
            segments.append(segment)

        concat_file = temp_dir / "segments.txt"
        concat_file.write_text(
            "\n".join(f"file '{_concat_escape(path.resolve())}'" for path in segments) + "\n",
            encoding="utf-8",
        )
        progress(len(ranges) * 1000, total_units, "Joining segments")
        command = [
            "ffmpeg", "-hide_banner", "-y", "-fflags", "+genpts",
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
) -> None:
    shape = shape or OutputShape()
    duration = clip.duration_ms
    if duration <= 0:
        raise ExportError("A clip has an invalid zero-length range.")
    if mode == "copy":
        # Seek 1 ms past the snapped keyframe so rounding never selects the previous one.
        command = [
            "ffmpeg", "-hide_banner", "-y",
            "-ss", ffmpeg_time(clip.start_ms + 1), "-i", str(source),
            "-t", ffmpeg_time(duration),
            "-map", "0:v:0",
        ]
        if include_audio:
            command.extend(["-map", "0:a?"])
        command.extend(["-c", "copy", "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", str(target)])
    elif mode == "accurate":
        command = [
            "ffmpeg", "-hide_banner", "-y",
            "-ss", ffmpeg_time(clip.start_ms), "-t", ffmpeg_time(duration),
            "-i", str(source),
            "-map", "0:v:0",
        ]
        if include_audio:
            command.extend(["-map", "0:a:0?"])
        before, after = shape.chain_for(clip)
        video_chain = ",".join(part for part in (before, video_filtergraph(filters, source_fps), after) if part)
        if video_chain:
            command.extend(["-vf", video_chain])
        command.extend(_video_encoder_arguments(
            "nvenc" if video_encoder == "nvenc" else "cpu", source_video_bitrate_kbps, shape.quality,
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
            logs.append("NVIDIA NVENC failed; retrying this clip with CPU libx264.")
            cpu_command = _replace_nvenc_with_libx264(command, source_video_bitrate_kbps, shape.quality)
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
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and "h264_nvenc" in completed.stdout


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


def _replace_nvenc_with_libx264(command: list[str], source_video_bitrate_kbps: int = 0, quality: int = 95) -> list[str]:
    start = command.index("-c:v")
    end = start + 2
    while end < len(command) and command[end] not in {"-c:a", "-af", "-movflags"}:
        end += 1
    return [
        *command[:start],
        *_video_encoder_arguments("cpu", source_video_bitrate_kbps, quality),
        *command[end:],
    ]


def _video_encoder_arguments(encoder: str, source_video_bitrate_kbps: int = 0, quality: int = 95) -> list[str]:
    """H.264 for re-encoded output. Quality 95 (and 100 when re-encoding is unavoidable) is
    about VMAF 98 against the source; each 5 points lower trades detail for a smaller file.

    The ceiling only stops a very compact source, such as AV1, from ballooning.
    """
    quality = max(50, min(95, int(quality)))
    crf = 16 + (95 - quality) * 0.4  # 95 -> 16, 90 -> 18, 80 -> 22, 70 -> 26, 50 -> 34
    source_rate = max(0, int(source_video_bitrate_kbps))
    ceiling = max(8_000, source_rate * 4) if source_rate else 0
    if encoder == "nvenc":
        arguments = [
            "-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq", "-profile:v", "high",
            "-rc", "vbr", "-cq", f"{round(crf) + 3}", "-b:v", "0", "-pix_fmt", "yuv420p",
        ]
    else:
        arguments = [
            "-c:v", "libx264", "-preset", "slow", "-crf", f"{round(crf)}", "-profile:v", "high",
            "-pix_fmt", "yuv420p",
        ]
    if ceiling:
        arguments.extend(["-maxrate", f"{ceiling}k", "-bufsize", f"{ceiling * 2}k"])
    return arguments


def keyframe_at_or_before(source: Path, milliseconds: int) -> int:
    """Timestamp of the last keyframe at or before `milliseconds`, read from a small window."""
    window_start = max(0.0, milliseconds / 1000 - 20)
    completed = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
            "-read_intervals", f"{window_start:.3f}%{milliseconds / 1000 + 0.05:.3f}",
            "-show_entries", "frame=pts_time", "-of", "csv=p=0", str(source),
        ],
        capture_output=True, text=True, timeout=60, check=False,
    )
    best = 0
    for line in completed.stdout.split():
        try:
            value = round(float(line.strip(",")) * 1000)
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
