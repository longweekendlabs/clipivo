from __future__ import annotations

import json
import subprocess
from fractions import Fraction
from pathlib import Path

from .models import StreamInfo, VideoInfo
from .downloads import tool_path


class ProbeError(RuntimeError):
    pass


def _run_ffprobe(args: list[str]) -> str:
    command = [tool_path("ffprobe"), "-v", "error", *args]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise ProbeError("ffprobe was not found. Install FFmpeg to open videos.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "Unknown ffprobe error").strip()
        raise ProbeError(detail) from exc
    return result.stdout


def probe_video(path: Path, include_keyframes: bool = True) -> VideoInfo:
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ProbeError(f"Video does not exist: {path}")

    payload = json.loads(_run_ffprobe([
        "-show_format", "-show_streams", "-of", "json", str(path),
    ]))
    raw_streams = payload.get("streams", [])
    video = next((stream for stream in raw_streams if stream.get("codec_type") == "video"), None)
    if video is None:
        raise ProbeError("The selected file contains no video stream.")

    duration_text = video.get("duration") or payload.get("format", {}).get("duration") or "0"
    duration_ms = max(0, round(float(duration_text) * 1000))
    fps_text = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"
    try:
        fps = float(Fraction(fps_text))
    except (ValueError, ZeroDivisionError):
        fps = 0.0

    streams = tuple(
        StreamInfo(
            index=int(stream.get("index", 0)),
            kind=str(stream.get("codec_type", "unknown")),
            codec=str(stream.get("codec_name", "unknown")),
            language=str(stream.get("tags", {}).get("language", "")),
            bit_rate_kbps=max(0, round(float(stream.get("bit_rate") or 0) / 1000)),
        )
        for stream in raw_streams
    )
    video_bitrate = max(0, round(float(video.get("bit_rate") or 0) / 1000))
    if not video_bitrate:
        format_bitrate = max(0, round(float(payload.get("format", {}).get("bit_rate") or 0) / 1000))
        audio_bitrate = sum(stream.bit_rate_kbps for stream in streams if stream.kind == "audio")
        video_bitrate = max(0, format_bitrate - audio_bitrate)
    keyframes = _probe_keyframes(path) if include_keyframes else ()
    return VideoInfo(
        path=path,
        duration_ms=duration_ms,
        width=int(video.get("width", 0)),
        height=int(video.get("height", 0)),
        fps=fps,
        container=str(payload.get("format", {}).get("format_name", path.suffix.lstrip("."))),
        video_codec=str(video.get("codec_name", "unknown")),
        video_bitrate_kbps=video_bitrate,
        streams=streams,
        keyframes_ms=keyframes,
    )


def _probe_keyframes(path: Path) -> tuple[int, ...]:
    output = _run_ffprobe([
        "-select_streams", "v:0",
        "-show_packets",
        "-show_entries", "packet=pts_time,flags",
        "-of", "csv=p=0",
        str(path),
    ])
    keyframes: list[int] = []
    for line in output.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2 or "K" not in parts[-1]:
            continue
        try:
            keyframes.append(max(0, round(float(parts[0]) * 1000)))
        except ValueError:
            continue
    if not keyframes or keyframes[0] != 0:
        keyframes.insert(0, 0)
    return tuple(sorted(set(keyframes)))
