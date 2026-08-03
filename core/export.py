from __future__ import annotations

import subprocess
import tempfile
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from threading import Event, Thread
from typing import Callable

from .filters import VideoFilter, active_filters, audio_filtergraph, video_filtergraph
from .models import ClipRange
from .timecode import ffmpeg_time


class ExportError(RuntimeError):
    pass


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
) -> list[Path]:
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
    if filters:
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
                    character_tail, source_video_bitrate_kbps,
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
                if len(enabled) == 1:
                    generated_name = f"{source.stem}_{output_tag}{character_tail}{suffix}"
                else:
                    generated_name = f"{source.stem}_{output_tag}_{index:03d}{character_tail}{suffix}"
                clip_target = target or _unique_path(output_dir / generated_name)
                _export_one(
                    source, clip, clip_target, mode, include_audio, cancel_event,
                    logs, filters, source_fps, resolved_encoder,
                    video_encoder == "auto", clip_progress, source_video_bitrate_kbps,
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
) -> Path:
    suffix = ".mp4"
    target = target or _unique_path(output_dir / f"{source.stem}_{output_tag}{character_tail}{suffix}")
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
                segment_progress, source_video_bitrate_kbps,
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
) -> None:
    duration = clip.duration_ms
    if duration <= 0:
        raise ExportError("A clip has an invalid zero-length range.")
    if mode == "copy":
        command = [
            "ffmpeg", "-hide_banner", "-y",
            "-ss", ffmpeg_time(clip.start_ms), "-i", str(source),
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
        video_chain = video_filtergraph(filters, source_fps)
        if video_chain:
            command.extend(["-vf", video_chain])
        if video_encoder == "nvenc":
            command.extend(_video_encoder_arguments("nvenc", source_video_bitrate_kbps))
        else:
            command.extend(_video_encoder_arguments("cpu", source_video_bitrate_kbps))
        if include_audio:
            audio_chain = audio_filtergraph(filters, source_fps)
            if audio_chain:
                command.extend(["-af", audio_chain])
            command.extend(["-c:a", "aac", "-b:a", "128k"])
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
            cpu_command = _replace_nvenc_with_libx264(command, source_video_bitrate_kbps)
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


def _replace_nvenc_with_libx264(command: list[str], source_video_bitrate_kbps: int = 0) -> list[str]:
    start = command.index("-c:v")
    end = start + 2
    while end < len(command) and command[end] not in {"-c:a", "-af", "-movflags"}:
        end += 1
    return [
        *command[:start],
        *_video_encoder_arguments("cpu", source_video_bitrate_kbps),
        *command[end:],
    ]


def _video_encoder_arguments(encoder: str, source_video_bitrate_kbps: int = 0) -> list[str]:
    """Use sane perceptual quality while preventing small sources from exploding in size."""
    source_rate = max(0, int(source_video_bitrate_kbps))
    ceiling = max(500, round(source_rate * 1.15)) if source_rate else 0
    if encoder == "nvenc":
        arguments = [
            "-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq",
            "-rc", "vbr", "-cq", "23", "-b:v", "0", "-pix_fmt", "yuv420p",
        ]
    else:
        arguments = [
            "-c:v", "libx264", "-preset", "medium", "-crf", "22",
            "-pix_fmt", "yuv420p",
        ]
    if ceiling:
        arguments.extend(["-maxrate", f"{ceiling}k", "-bufsize", f"{ceiling * 2}k"])
    return arguments


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
