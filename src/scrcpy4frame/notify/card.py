"""Renders a notification as an RGBA card image for the VR overlay."""

from __future__ import annotations

import base64

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath

from .router import Toast

WIDTH, HEIGHT = 640, 168


def render_card(toast: Toast) -> QImage:
    image = QImage(WIDTH, HEIGHT, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                     | QPainter.RenderHint.SmoothPixmapTransform)
    card = QPainterPath()
    card.addRoundedRect(QRectF(2, 2, WIDTH - 4, HEIGHT - 4), 28, 28)
    p.fillPath(card, QColor(24, 27, 34, 238))
    p.setPen(QColor(255, 255, 255, 40))
    p.drawPath(card)

    left = 24
    icon = _decode_icon(toast.icon_b64)
    if icon is not None:
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(left, 36, 96, 96), 22, 22)
        p.save()
        p.setClipPath(clip)
        p.drawImage(QRectF(left, 36, 96, 96), icon)
        p.restore()
        left += 96 + 22

    text_width = WIDTH - left - 24
    label_font = QFont()
    label_font.setPixelSize(22)
    title_font = QFont()
    title_font.setPixelSize(30)
    title_font.setBold(True)
    body_font = QFont()
    body_font.setPixelSize(25)

    p.setFont(label_font)
    p.setPen(QColor(160, 168, 184))
    p.drawText(QRectF(left, 18, text_width, 30), Qt.AlignmentFlag.AlignVCenter,
               QFontMetrics(label_font).elidedText(toast.app_label, Qt.TextElideMode.ElideRight, text_width))
    p.setFont(title_font)
    p.setPen(QColor(244, 246, 250))
    p.drawText(QRectF(left, 50, text_width, 40), Qt.AlignmentFlag.AlignVCenter,
               QFontMetrics(title_font).elidedText(toast.title, Qt.TextElideMode.ElideRight, text_width))
    p.setFont(body_font)
    p.setPen(QColor(214, 219, 228))
    p.drawText(QRectF(left, 94, text_width, 62), Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignTop,
               _elide_lines(toast.text, body_font, text_width, 2))
    p.end()
    return image.convertToFormat(QImage.Format.Format_RGBA8888)


def rgba_bytes(image: QImage) -> bytes:
    data = bytes(image.constBits())
    row = image.width() * 4
    if image.bytesPerLine() == row:
        return data[: row * image.height()]
    return b"".join(data[y * image.bytesPerLine(): y * image.bytesPerLine() + row]
                    for y in range(image.height()))


def _decode_icon(icon_b64: str | None) -> QImage | None:
    if not icon_b64:
        return None
    try:
        image = QImage.fromData(base64.b64decode(icon_b64))
    except ValueError:
        return None
    return None if image.isNull() else image


def _elide_lines(text: str, font: QFont, width: int, lines: int) -> str:
    """Greedy word wrap into at most `lines` lines, eliding the last one."""
    metrics = QFontMetrics(font)
    words = text.split()
    out: list[str] = []
    current = ""
    i = 0
    while i < len(words) and len(out) < lines - 1:
        candidate = f"{current} {words[i]}".strip()
        if not current or metrics.horizontalAdvance(candidate) <= width:
            current = candidate
            i += 1
        else:
            out.append(current)
            current = ""
    rest = " ".join(([current] if current else []) + words[i:])
    if rest:
        out.append(metrics.elidedText(rest, Qt.TextElideMode.ElideRight, width))
    return "\n".join(out)
