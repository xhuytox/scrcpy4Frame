#!/usr/bin/env python3
"""Generate flatpak/data/icons/128x128.png from the SVG app icon.

It's the app's window icon, which gamescope turns into the Frame's dashboard tab icon. A fixed
128 px image keeps that exact under the Frame's 1.5x scale. Rerun and commit the PNG after
changing the SVG.
"""

import os
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "flatpak/data"
SVG = DATA / "io.github.xhuytox.Scrcpy4Frame.svg"
SIZE = 128


def main() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    app = QGuiApplication(sys.argv[:1])  # noqa: F841 - QPainter needs one
    renderer = QSvgRenderer(str(SVG))
    if not renderer.isValid():
        sys.exit(f"couldn't read {SVG}")
    image = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    out = DATA / f"icons/{SIZE}x{SIZE}.png"
    out.parent.mkdir(exist_ok=True)
    if not image.save(str(out)):
        sys.exit(f"couldn't write {out}")
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
