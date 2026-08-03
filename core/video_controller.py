from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import av


class DecodeError(RuntimeError):
    pass


@dataclass
class DecodedFrame:
    timestamp_ms: int
    width: int
    height: int
    rgb: bytes
    line_size: int


class VideoController:
    """Precise paused-frame decoder backed by PyAV.

    Continuous playback is handled by libVLC. This controller is used for
    exact frame stepping and deterministic thumbnail or scrub extraction.
    """

    def __init__(self, path: Path, *, keyframes_only: bool = False, preview_max_width: int = 0):
        self.path = Path(path)
        self.preview_max_width = max(0, int(preview_max_width))
        try:
            self.container = av.open(str(self.path))
        except Exception as exc:
            raise DecodeError(f"Could not open video decoder: {exc}") from exc
        try:
            self.stream = self.container.streams.video[0]
        except IndexError as exc:
            self.container.close()
            raise DecodeError("The selected file has no decodable video stream.") from exc
        self.stream.thread_type = "AUTO"
        if keyframes_only:
            self.stream.codec_context.skip_frame = "NONKEY"
        guessed_rate = self.stream.average_rate or self.stream.guessed_rate
        self.frame_interval_ms = max(1, round(1000 / float(guessed_rate))) if guessed_rate else 40

    def close(self) -> None:
        self.container.close()

    def first_frame(self) -> DecodedFrame:
        return self.frame_near(0, direction="next", include_current=True)

    def next_frame(self, timestamp_ms: int) -> DecodedFrame:
        return self.frame_near(timestamp_ms, direction="next", include_current=False)

    def previous_frame(self, timestamp_ms: int) -> DecodedFrame:
        return self.frame_near(timestamp_ms, direction="previous", include_current=False)

    def frame_at(self, timestamp_ms: int) -> DecodedFrame:
        return self.frame_near(timestamp_ms, direction="previous", include_current=True)

    def frame_near(self, timestamp_ms: int, direction: str, include_current: bool) -> DecodedFrame:
        target_ms = max(0, int(timestamp_ms))
        lookback_ms = max(1500, self.frame_interval_ms * 12)
        seek_ms = max(0, target_ms - lookback_ms)
        time_base = float(self.stream.time_base)
        self.container.seek(int((seek_ms / 1000) / time_base), stream=self.stream, backward=True, any_frame=False)

        previous: DecodedFrame | None = None
        tolerance = max(1, self.frame_interval_ms // 4)
        for frame in self.container.decode(self.stream):
            if frame.pts is None:
                continue
            frame_ms = round(float(frame.pts * self.stream.time_base) * 1000)
            decoded = self._convert(frame, frame_ms)
            if direction == "next":
                boundary = target_ms - tolerance if include_current else target_ms + tolerance
                if frame_ms >= boundary:
                    return decoded
            else:
                boundary = target_ms + tolerance if include_current else target_ms - tolerance
                if frame_ms > boundary:
                    break
                previous = decoded
            if frame_ms > target_ms + max(2000, lookback_ms):
                break

        if previous is not None:
            return previous
        raise DecodeError(f"Could not decode a frame near {target_ms / 1000:.3f}s.")

    def _convert(self, frame, timestamp_ms: int) -> DecodedFrame:
        width, height = frame.width, frame.height
        if self.preview_max_width and width > self.preview_max_width:
            scale = self.preview_max_width / width
            width = self.preview_max_width
            height = max(2, round(height * scale))
        rgb_frame = frame.reformat(width=width, height=height, format="rgb24")
        plane = rgb_frame.planes[0]
        return DecodedFrame(
            timestamp_ms=max(0, timestamp_ms),
            width=rgb_frame.width,
            height=rgb_frame.height,
            rgb=bytes(plane),
            line_size=plane.line_size,
        )
