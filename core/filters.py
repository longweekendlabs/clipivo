from __future__ import annotations

from dataclasses import asdict, dataclass, field
from uuid import uuid4


FILTER_CATALOG = {
    "crop": {
        "name": "Crop",
        "category": "Transformation",
        "description": "Remove pixels from the left, right, top, and bottom edges.",
        "defaults": {"left": 0, "right": 0, "top": 0, "bottom": 0},
    },
    "rotate": {
        "name": "Rotate",
        "category": "Transformation",
        "description": "Rotate the image by 90, 180, or 270 degrees.",
        "defaults": {"angle": 90},
    },
    "vertical_flip": {
        "name": "Vertical flip",
        "category": "Transformation",
        "description": "Flip the image vertically.",
        "defaults": {},
    },
    "frame_rate": {
        "name": "Frame rate",
        "category": "Transformation",
        "description": "Resample while keeping duration, or change speed for slow/fast motion.",
        "defaults": {"mode": "resample", "fps": 25.0, "blend": False},
    },
    "contrast": {
        "name": "Contrast",
        "category": "Colors",
        "description": "Adjust image contrast and brightness.",
        "defaults": {
            "contrast": 1.0, "brightness": 0.0,
            "luma": True, "chroma_u": True, "chroma_v": True,
        },
    },
    "color_balance": {
        "name": "Color balance",
        "category": "Colors",
        "description": "Adjust luma, hue shift, and saturation independently for shadows, midtones, and highlights.",
        "defaults": {
            "shadow_luma": 0.0, "shadow_angle": 0.0, "shadow_shift": 0.0, "shadow_saturation": 0.0,
            "midtone_luma": 0.0, "midtone_angle": 0.0, "midtone_shift": 0.0, "midtone_saturation": 0.0,
            "highlight_luma": 0.0, "highlight_angle": 0.0, "highlight_shift": 0.0, "highlight_saturation": 0.0,
        },
    },
}


@dataclass
class VideoFilter:
    kind: str
    params: dict = field(default_factory=dict)
    enabled: bool = True
    id: str = field(default_factory=lambda: uuid4().hex)

    @property
    def name(self) -> str:
        return str(FILTER_CATALOG.get(self.kind, {}).get("name", self.kind))

    @property
    def category(self) -> str:
        return str(FILTER_CATALOG.get(self.kind, {}).get("category", "Other"))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "VideoFilter":
        kind = str(data["kind"])
        if kind not in FILTER_CATALOG:
            raise ValueError(f"Unknown video filter: {kind}")
        defaults = dict(FILTER_CATALOG[kind]["defaults"])
        defaults.update(dict(data.get("params", {})))
        return cls(
            id=str(data.get("id") or uuid4().hex),
            kind=kind,
            params=defaults,
            enabled=bool(data.get("enabled", True)),
        )


def create_filter(kind: str, source_fps: float = 25.0) -> VideoFilter:
    if kind not in FILTER_CATALOG:
        raise ValueError(f"Unknown video filter: {kind}")
    params = dict(FILTER_CATALOG[kind]["defaults"])
    if kind == "frame_rate" and source_fps > 0:
        params["fps"] = round(float(source_fps), 3)
    return VideoFilter(kind=kind, params=params)


def active_filters(filters: list[VideoFilter] | None) -> list[VideoFilter]:
    return [item for item in (filters or []) if item.enabled and item.kind in FILTER_CATALOG]


def video_filtergraph(
    filters: list[VideoFilter] | None,
    source_fps: float,
    *,
    include_timing: bool = True,
) -> str:
    chain: list[str] = []
    for item in active_filters(filters):
        params = item.params
        if item.kind == "crop":
            left = _even(params.get("left", 0))
            right = _even(params.get("right", 0))
            top = _even(params.get("top", 0))
            bottom = _even(params.get("bottom", 0))
            if any((left, right, top, bottom)):
                chain.append(f"crop=iw-{left + right}:ih-{top + bottom}:{left}:{top}")
        elif item.kind == "rotate":
            angle = int(params.get("angle", 90)) % 360
            if angle == 90:
                chain.append("transpose=clock")
            elif angle == 180:
                chain.extend(("hflip", "vflip"))
            elif angle == 270:
                chain.append("transpose=cclock")
        elif item.kind == "vertical_flip":
            chain.append("vflip")
        elif item.kind == "frame_rate" and include_timing:
            target_fps = max(0.001, float(params.get("fps", source_fps or 25.0)))
            if params.get("mode") == "speed":
                factor = max(0.001, float(source_fps or target_fps) / target_fps)
                chain.extend((f"setpts={factor:.8f}*PTS", f"fps={target_fps:.6f}"))
            elif bool(params.get("blend", False)):
                chain.append(f"minterpolate=fps={target_fps:.6f}:mi_mode=blend")
            else:
                chain.append(f"fps={target_fps:.6f}")
        elif item.kind == "contrast":
            contrast = min(1.5, max(0.5, float(params.get("contrast", 1.0))))
            brightness = min(1.0, max(-1.0, float(params.get("brightness", 0.0))))
            offset = brightness * 127.0
            planes: list[str] = []
            if bool(params.get("luma", True)):
                planes.append(f"y='clip(val*{contrast:.5f}+{offset:.5f}\\,0\\,255)'")
            if bool(params.get("chroma_u", True)):
                planes.append(f"u='clip((val-128)*{contrast:.5f}+128\\,0\\,255)'")
            if bool(params.get("chroma_v", True)):
                planes.append(f"v='clip((val-128)*{contrast:.5f}+128\\,0\\,255)'")
            if planes:
                chain.append("lutyuv=" + ":".join(planes))
        elif item.kind == "color_balance":
            balance: list[str] = []
            for prefix, suffix in (("shadow", "s"), ("midtone", "m"), ("highlight", "h")):
                angle = float(params.get(f"{prefix}_angle", 0.0))
                shift = min(1.0, max(0.0, float(params.get(f"{prefix}_shift", 0.0))))
                red, green, blue = _balance_components(angle, shift)
                balance.extend((f"r{suffix}={red:.5f}", f"g{suffix}={green:.5f}", f"b{suffix}={blue:.5f}"))
            balance.append("pl=1")
            chain.append("colorbalance=" + ":".join(balance))
            low = min(1.0, max(0.0, float(params.get("shadow_luma", 0.0)) * 0.25))
            mid = min(1.0, max(0.0, 0.5 + float(params.get("midtone_luma", 0.0)) * 0.25))
            high = min(1.0, max(0.0, 1.0 + float(params.get("highlight_luma", 0.0)) * 0.25))
            if any(abs(float(params.get(f"{prefix}_luma", 0.0))) > 0.0001 for prefix in ("shadow", "midtone", "highlight")):
                chain.append(f"curves=master='0/{low:.5f} 0.5/{mid:.5f} 1/{high:.5f}'")
            saturation = 1.0 + sum(float(params.get(f"{prefix}_saturation", 0.0)) for prefix in ("shadow", "midtone", "highlight")) / 3.0
            if abs(saturation - 1.0) > 0.0001:
                chain.append(f"eq=saturation={min(2.0, max(0.0, saturation)):.5f}")
    return ",".join(chain)


def audio_filtergraph(filters: list[VideoFilter] | None, source_fps: float) -> str:
    speed = 1.0
    for item in active_filters(filters):
        if item.kind != "frame_rate" or item.params.get("mode") != "speed":
            continue
        target_fps = max(0.001, float(item.params.get("fps", source_fps or 25.0)))
        speed *= target_fps / max(0.001, float(source_fps or target_fps))
    if abs(speed - 1.0) < 0.0001:
        return ""
    return ",".join(f"atempo={value:.8f}" for value in _atempo_factors(speed))


def filtered_dimensions(filters: list[VideoFilter] | None, width: int, height: int) -> tuple[int, int]:
    current_width, current_height = width, height
    for item in active_filters(filters):
        if item.kind == "crop":
            current_width = max(2, current_width - _even(item.params.get("left", 0)) - _even(item.params.get("right", 0)))
            current_height = max(2, current_height - _even(item.params.get("top", 0)) - _even(item.params.get("bottom", 0)))
        elif item.kind == "rotate" and int(item.params.get("angle", 90)) % 180:
            current_width, current_height = current_height, current_width
    return current_width, current_height


def describe_filter(item: VideoFilter) -> str:
    params = item.params
    if item.kind == "crop":
        return f"Crop · L{_even(params.get('left', 0))} R{_even(params.get('right', 0))} T{_even(params.get('top', 0))} B{_even(params.get('bottom', 0))}"
    if item.kind == "rotate":
        return f"Rotate · {int(params.get('angle', 90))}°"
    if item.kind == "vertical_flip":
        return "Vertical flip"
    if item.kind == "frame_rate":
        mode = "Speed" if params.get("mode") == "speed" else "Resample"
        return f"Frame rate · {mode} {float(params.get('fps', 25.0)):.3f} fps"
    if item.kind == "contrast":
        return f"Contrast · {float(params.get('contrast', 1.0)):.2f} / {float(params.get('brightness', 0.0)):+.2f}"
    if item.kind == "color_balance":
        return "Color balance · shadows / midtones / highlights"
    return item.name


def _even(value) -> int:
    return max(0, int(value)) // 2 * 2


def _atempo_factors(speed: float) -> list[float]:
    factors: list[float] = []
    while speed < 0.5:
        factors.append(0.5)
        speed /= 0.5
    while speed > 2.0:
        factors.append(2.0)
        speed /= 2.0
    factors.append(speed)
    return factors


def _balance_components(angle: float, shift: float) -> tuple[float, float, float]:
    from math import cos, pi

    radians = angle * pi / 180.0
    values = (
        shift * cos(radians),
        shift * cos(radians - 2.0 * pi / 3.0),
        shift * cos(radians + 2.0 * pi / 3.0),
    )
    return tuple(min(1.0, max(-1.0, value)) for value in values)
