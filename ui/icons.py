from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF


def line_icon(kind: str, color: str = "#ccd3db", size: int = 24) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), max(1.5, size / 14), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    s = float(size)

    if kind == "open":
        painter.drawPath(_folder_path(s))
    elif kind == "save":
        painter.drawRoundedRect(QRectF(s * .2, s * .13, s * .6, s * .73), 1.5, 1.5)
        painter.drawRect(QRectF(s * .32, s * .13, s * .35, s * .27))
        painter.drawRoundedRect(QRectF(s * .32, s * .57, s * .36, s * .29), 1.5, 1.5)
    elif kind == "person":
        painter.drawEllipse(QRectF(s * .36, s * .12, s * .28, s * .28))
        painter.drawArc(QRectF(s * .2, s * .44, s * .6, s * .47), 0, 180 * 16)
    elif kind == "film":
        painter.drawRoundedRect(QRectF(s * .22, s * .12, s * .56, s * .76), 2, 2)
        for y in (.24, .48, .72):
            painter.drawLine(QPointF(s * .22, s * y), QPointF(s * .31, s * y))
            painter.drawLine(QPointF(s * .69, s * y), QPointF(s * .78, s * y))
    elif kind == "settings":
        painter.drawEllipse(QRectF(s * .38, s * .38, s * .24, s * .24))
        painter.drawEllipse(QRectF(s * .21, s * .21, s * .58, s * .58))
        for angle in range(0, 360, 45):
            painter.save()
            painter.translate(s / 2, s / 2)
            painter.rotate(angle)
            painter.drawLine(QPointF(0, -s * .29), QPointF(0, -s * .4))
            painter.restore()
    elif kind == "filters":
        for y, knob in ((.28, .36), (.5, .66), (.72, .45)):
            painter.drawLine(QPointF(s * .18, s * y), QPointF(s * .82, s * y))
            painter.setBrush(QColor(color))
            painter.drawEllipse(QRectF(s * knob - s * .065, s * y - s * .065, s * .13, s * .13))
            painter.setBrush(Qt.NoBrush)
    elif kind == "batch":
        for y in (.25, .5, .75):
            painter.drawRoundedRect(QRectF(s * .17, s * y - s * .055, s * .11, s * .11), 1, 1)
            painter.drawLine(QPointF(s * .38, s * y), QPointF(s * .79, s * y))
    elif kind in {"play", "pause", "next", "previous", "forward", "back", "frame_back", "frame_forward"}:
        _paint_transport(painter, kind, s)
    elif kind == "selection_play":
        painter.drawLine(QPointF(s * .18, s * .22), QPointF(s * .18, s * .78))
        painter.drawLine(QPointF(s * .18, s * .22), QPointF(s * .31, s * .22))
        painter.drawLine(QPointF(s * .18, s * .78), QPointF(s * .31, s * .78))
        painter.drawLine(QPointF(s * .82, s * .22), QPointF(s * .82, s * .78))
        painter.drawLine(QPointF(s * .69, s * .22), QPointF(s * .82, s * .22))
        painter.drawLine(QPointF(s * .69, s * .78), QPointF(s * .82, s * .78))
        painter.setBrush(QColor(color))
        painter.setPen(Qt.NoPen)
        painter.drawPolygon(QPolygonF([
            QPointF(s * .4, s * .31), QPointF(s * .4, s * .69), QPointF(s * .64, s * .5),
        ]))
    elif kind in {"volume", "muted"}:
        painter.setBrush(QColor(color))
        painter.setPen(Qt.NoPen)
        painter.drawPolygon(QPolygonF([
            QPointF(s * .16, s * .4), QPointF(s * .34, s * .4), QPointF(s * .53, s * .23),
            QPointF(s * .53, s * .77), QPointF(s * .34, s * .6), QPointF(s * .16, s * .6),
        ]))
        painter.setBrush(Qt.NoBrush)
        painter.setPen(pen)
        if kind == "volume":
            painter.drawArc(QRectF(s * .42, s * .3, s * .33, s * .4), -60 * 16, 120 * 16)
            painter.drawArc(QRectF(s * .38, s * .2, s * .5, s * .6), -58 * 16, 116 * 16)
        else:
            painter.drawLine(QPointF(s * .66, s * .35), QPointF(s * .86, s * .65))
            painter.drawLine(QPointF(s * .86, s * .35), QPointF(s * .66, s * .65))
    elif kind == "stop":
        painter.setBrush(QColor(color))
        painter.setPen(Qt.NoPen)
        painter.drawRect(QRectF(s * .31, s * .31, s * .38, s * .38))
    elif kind in {"chevron_left", "chevron_right", "chevron_up", "chevron_down"}:
        points = {
            "chevron_left": [(0.62, .25), (.38, .5), (.62, .75)],
            "chevron_right": [(.38, .25), (.62, .5), (.38, .75)],
            "chevron_up": [(.25, .62), (.5, .38), (.75, .62)],
            "chevron_down": [(.25, .38), (.5, .62), (.75, .38)],
        }[kind]
        painter.drawPolyline(QPolygonF([QPointF(s * x, s * y) for x, y in points]))
    painter.end()
    return QIcon(pixmap)


def _folder_path(s: float) -> QPainterPath:
    path = QPainterPath(QPointF(s * .12, s * .34))
    path.lineTo(s * .35, s * .34)
    path.lineTo(s * .43, s * .22)
    path.lineTo(s * .78, s * .22)
    path.lineTo(s * .84, s * .38)
    path.lineTo(s * .74, s * .79)
    path.lineTo(s * .16, s * .79)
    path.closeSubpath()
    return path


def _triangle(s: float, x: float, direction: int = 1) -> QPolygonF:
    if direction > 0:
        return QPolygonF([QPointF(s * x, s * .27), QPointF(s * x, s * .73), QPointF(s * (x + .26), s * .5)])
    return QPolygonF([QPointF(s * x, s * .27), QPointF(s * x, s * .73), QPointF(s * (x - .26), s * .5)])


def _paint_transport(painter: QPainter, kind: str, s: float) -> None:
    color = painter.pen().color()
    if kind in {"frame_back", "frame_forward"}:
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(color, max(1.7, s / 12), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        if kind == "frame_back":
            painter.drawPolyline(QPolygonF([
                QPointF(s * .57, s * .27), QPointF(s * .34, s * .5), QPointF(s * .57, s * .73),
            ]))
            painter.drawLine(QPointF(s * .69, s * .29), QPointF(s * .69, s * .71))
        else:
            painter.drawPolyline(QPolygonF([
                QPointF(s * .43, s * .27), QPointF(s * .66, s * .5), QPointF(s * .43, s * .73),
            ]))
            painter.drawLine(QPointF(s * .31, s * .29), QPointF(s * .31, s * .71))
        return
    painter.setBrush(color)
    painter.setPen(Qt.NoPen)
    if kind == "play":
        painter.drawPolygon(_triangle(s, .36))
    elif kind == "pause":
        painter.drawRoundedRect(QRectF(s * .29, s * .25, s * .14, s * .5), 1, 1)
        painter.drawRoundedRect(QRectF(s * .57, s * .25, s * .14, s * .5), 1, 1)
    elif kind == "forward":
        painter.drawPolygon(_triangle(s, .22))
        painter.drawPolygon(_triangle(s, .5))
    elif kind == "back":
        painter.drawPolygon(_triangle(s, .5, -1))
        painter.drawPolygon(_triangle(s, .78, -1))
    elif kind == "next":
        painter.drawPolygon(_triangle(s, .3))
        painter.drawRect(QRectF(s * .68, s * .26, s * .07, s * .48))
    elif kind == "previous":
        painter.drawPolygon(_triangle(s, .7, -1))
        painter.drawRect(QRectF(s * .25, s * .26, s * .07, s * .48))
