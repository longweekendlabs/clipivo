from __future__ import annotations


def format_timecode(milliseconds: int | None) -> str:
    if milliseconds is None:
        return "--:--:--.---"
    value = max(0, int(milliseconds))
    hours, remainder = divmod(value, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def ffmpeg_time(milliseconds: int) -> str:
    return f"{max(0, milliseconds) / 1000:.3f}"



def format_duration(milliseconds: int) -> str:
    """Short human duration: 4:01 or 1:02:03."""
    total = max(0, round(int(milliseconds) / 1000))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"
