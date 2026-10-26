from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True)
class StreamInfo:
    index: int
    kind: str
    codec: str
    language: str = ""
    bit_rate_kbps: int = 0


@dataclass(frozen=True)
class VideoInfo:
    path: Path
    duration_ms: int
    width: int
    height: int
    fps: float
    container: str
    video_codec: str
    video_bitrate_kbps: int = 0
    streams: tuple[StreamInfo, ...] = ()
    keyframes_ms: tuple[int, ...] = ()


@dataclass
class ClipRange:
    start_ms: int
    end_ms: int
    origin: str = "manual"
    enabled: bool = True
    id: str = field(default_factory=lambda: uuid4().hex)
    # Person framing data from tracking: camera shots with face samples [t_ms, cx, cy, face_h]
    # and the top of any static bottom band, plus the frame size they were measured on.
    # None for clips marked by hand or scanned without Crop to person.
    shots: list[dict] | None = None
    frame: list[int] | None = None

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ClipRange":
        return cls(
            id=str(data.get("id") or uuid4().hex),
            start_ms=int(data["start_ms"]),
            end_ms=int(data["end_ms"]),
            origin=str(data.get("origin", "manual")),
            enabled=bool(data.get("enabled", True)),
            shots=list(data["shots"]) if data.get("shots") else None,
            frame=[int(value) for value in data["frame"]] if data.get("frame") else None,
        )
