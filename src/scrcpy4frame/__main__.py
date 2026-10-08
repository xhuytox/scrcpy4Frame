"""App entry point: platform setup, logging and the QML engine."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import signal
import sys

from . import __version__, paths

THEME = {
    "bg": "#0f1115", "surface": "#181b22", "surface2": "#21252e", "surface3": "#2b303c",
    "border": "#333a48", "text": "#f2f4f8", "textDim": "#a3abba", "accent": "#5aa2ff",
    "accentText": "#08111f", "ok": "#3ecf8e", "warn": "#f5b544", "error": "#ff6b6b",
    "focus": "#ffd25a",
    "font": 28, "fontSmall": 22, "fontLarge": 36, "fontTitle": 46, "fontLabel": 20,
    "radius": 20, "target": 96, "gap": 20, "pad": 32,
    # Settings rows: control column width, and the row width below which controls stack under the label.
    "controlColumn": 720, "stackBelow": 1100,
}


def detect_game_mode() -> bool:
    """True under gamescope (Steam Game Mode / the Frame's VR shell)."""
    return (os.environ.get("XDG_CURRENT_DESKTOP", "").lower() == "gamescope"
            or "GAMESCOPE_WAYLAND_DISPLAY" in os.environ
            or os.environ.get("SteamGamepadUI") == "1")


def setup_logging(level: str) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    try:
        handlers.append(logging.handlers.RotatingFileHandler(
            paths.cache_dir() / "scrcpy4frame.log", maxBytes=1_000_000, backupCount=2))
    except OSError:
        pass
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def window_icon():
    """The app icon as a single 128 px image. On the Frame, gamescope makes a window's SteamVR
    dashboard tab icon from the first image in its _NET_WM_ICON, read when the window first maps."""
    from PySide6.QtGui import QIcon, QPixmap

    path = paths.app_icon_path()
    return QIcon(QPixmap(str(path))) if path else QIcon()


def build_ui(controller) -> tuple[object, list[str]]:
    """Returns (engine, warnings). The warnings list keeps filling as QML reports problems."""
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine

    engine = QQmlApplicationEngine()
    warnings: list[str] = []
    engine.warnings.connect(lambda items: warnings.extend(w.toString() for w in items))
    engine.rootContext().setContextProperty("app", controller)
    engine.rootContext().setContextProperty("theme", THEME)
    engine.load(QUrl.fromLocalFile(str(paths.qml_dir() / "Main.qml")))
    return engine, warnings


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["cli"]:
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
        from .cli import main as cli_main
        return cli_main(argv[1:])

    parser = argparse.ArgumentParser(prog="scrcpy4frame")
    parser.add_argument("--windowed", action="store_true", help="never start fullscreen")
    parser.add_argument("--game-mode", action="store_true", help="behave as if running under gamescope")
    parser.add_argument("--smoke", action="store_true", help="load the UI, check for QML errors, exit")
    parser.add_argument("--log-level", default=os.environ.get("SCRCPY4FRAME_LOG", "info"))
    args = parser.parse_args(argv)

    game_mode = (args.game_mode or detect_game_mode()) and not args.windowed
    if game_mode and "QT_QPA_PLATFORM" not in os.environ:
        # gamescope only accepts Wayland clients when started with --expose-wayland. X11 always works.
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    setup_logging(args.log_level)
    log = logging.getLogger("scrcpy4frame")
    log.info("scrcpy4Frame %s starting (flatpak=%s, game_mode=%s)", __version__, paths.in_flatpak(), game_mode)

    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuickControls2 import QQuickStyle

    from . import scrolling
    from .controller import AppController

    QGuiApplication.setApplicationName(paths.APP_NAME)
    QGuiApplication.setApplicationDisplayName(paths.APP_NAME)
    QGuiApplication.setDesktopFileName(paths.APP_ID)
    QQuickStyle.setStyle("Basic")
    qt_app = QGuiApplication(sys.argv[:1])
    qt_app.setWindowIcon(window_icon())     # before any window maps: the dashboard tab icon in VR
    scrolling.install(qt_app)       # faster wheel scrolling app-wide

    controller = AppController(game_mode=game_mode)
    engine, warnings = build_ui(controller)
    if not engine.rootObjects():
        log.error("failed to load the UI: %s", "; ".join(warnings))
        return 1

    if args.smoke:
        QTimer.singleShot(1500, qt_app.quit)
        qt_app.exec()
        found = list(warnings)
        del engine
        for warning in found:
            print("QML:", warning, file=sys.stderr)
        return 1 if found else 0

    # Let Python see SIGTERM/SIGINT (Steam's "Exit game") while Qt's event loop is running.
    signal.signal(signal.SIGTERM, lambda *_: qt_app.quit())
    signal.signal(signal.SIGINT, lambda *_: qt_app.quit())
    heartbeat = QTimer(interval=300, timeout=lambda: None)
    heartbeat.start()

    qt_app.aboutToQuit.connect(controller.shutdown)
    controller.start()
    code = qt_app.exec()
    del engine          # tear down QML before the controller it binds to
    return code


if __name__ == "__main__":
    sys.exit(main())
