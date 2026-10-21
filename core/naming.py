"""Output file names: the user picks a pattern, Clipivo fills it in."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from pathlib import Path

NAMING_SCHEMES = {
    "source_date": "Video name + date",
    "source": "Same as video",
    "custom_id": "Custom name + video ID",
    "custom_id_date": "Custom name + video ID + date",
}
DEFAULT_SCHEME = "source_date"

_BRACKET_ID = re.compile(r"\[([A-Za-z0-9_-]{6,})\]\s*$")
_YOUTUBE_ID = re.compile(r"[-_ ]([A-Za-z0-9_-]{11})$")


def video_id(stem: str) -> str:
    """The ID a downloader left in the file name: 'Title [abc123]' or 'Title-gvj96ngMgwY'."""
    for pattern in (_BRACKET_ID, _YOUTUBE_ID):
        match = pattern.search(stem)
        if match:
            return match.group(1)
    return ""


def safe_component(value: str) -> str:
    cleaned = unicodedata.normalize("NFC", str(value)).strip()
    cleaned = re.sub(r'[\x00-\x1f/\\:*?"<>|]+', "_", cleaned)
    cleaned = re.sub(r"\s+", "_", cleaned)
    return re.sub(r"_+", "_", cleaned).strip("._")


def date_stamp(when: datetime) -> str:
    return f"{when:%Y%m%d-%H%M%S}"


def output_base_name(
    source: Path,
    scheme: str = DEFAULT_SCHEME,
    custom: str = "",
    person: str = "",
    when: datetime | None = None,
) -> str:
    """Base file name without extension. `person` is added only when one video yields several people."""
    when = when or datetime.now()
    stem = Path(source).stem
    custom_name = safe_component(custom) or "clipivo"
    identifier = video_id(stem) or safe_component(stem) or "video"
    if scheme == "source":
        base = stem
    elif scheme == "custom_id":
        base = f"{custom_name}.{identifier}"
    elif scheme == "custom_id_date":
        base = f"{custom_name}.{identifier}.{date_stamp(when)}"
    else:
        base = f"{stem}_{date_stamp(when)}"
    if person:
        base += f"_{safe_component(person) or 'person'}"
    return base
