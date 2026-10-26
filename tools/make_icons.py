"""Render the app icon from assets/clipivo.svg into the formats packages need.

assets/icons/clipivo.png (512 px, Linux packages and the macOS .icns source) and
assets/icons/clipivo.ico (16-256 px, Windows). Run after changing the SVG:
    python tools/make_icons.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parents[1]


def render(renderer: QSvgRenderer, size: int) -> QImage:
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter)
    painter.end()
    return image


def png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def write_ico(path: Path, images: list[QImage]) -> None:
    """A Windows icon holding PNG-compressed images, which Windows Vista and later read."""
    entries = [(image.width(), png_bytes(image)) for image in images]
    header = (0).to_bytes(2, "little") + (1).to_bytes(2, "little") + len(entries).to_bytes(2, "little")
    offset = 6 + 16 * len(entries)
    directory, payload = b"", b""
    for size, data in entries:
        dimension = 0 if size >= 256 else size
        directory += bytes([dimension, dimension, 0, 0]) + (1).to_bytes(2, "little") + (32).to_bytes(2, "little")
        directory += len(data).to_bytes(4, "little") + offset.to_bytes(4, "little")
        payload += data
        offset += len(data)
    path.write_bytes(header + directory + payload)


def main() -> int:
    app = QGuiApplication(sys.argv)  # noqa: F841 - Qt needs an application to paint
    renderer = QSvgRenderer(str(ROOT / "assets" / "clipivo.svg"))
    if not renderer.isValid():
        print("assets/clipivo.svg could not be read", file=sys.stderr)
        return 1
    target = ROOT / "assets" / "icons"
    target.mkdir(parents=True, exist_ok=True)
    render(renderer, 512).save(str(target / "clipivo.png"))
    write_ico(target / "clipivo.ico", [render(renderer, size) for size in (16, 24, 32, 48, 64, 128, 256)])
    print("wrote", target / "clipivo.png", "and", target / "clipivo.ico")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
