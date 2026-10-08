"""scrcpy command line and failure classification, without Qt (the controller runs the process)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .settings import Settings

EXIT_OK = 0
EXIT_START_FAILED = 1
EXIT_DISCONNECTED = 2

# LAlt+Q (or Super+Q) quits scrcpy, so a Steam Input chord that sends LAlt+Q can close it.
SHORTCUT_MOD = "lalt,lsuper"


def build_args(scrcpy: str, serial: str, title: str, settings: Settings,
               max_size_override: int | None = None) -> list[str]:
    args = [
        scrcpy,
        f"--serial={serial}",
        f"--window-title={title}",
        f"--video-codec={settings.video_codec}",
        f"--max-size={max_size_override or settings.max_size}",
        f"--max-fps={settings.max_fps}",
        f"--video-bit-rate={settings.bitrate_mbps}M",
        "--video-buffer=30",
        f"--shortcut-mod={SHORTCUT_MOD}",
        "--no-audio",                     # cast separately, on request (audiocast.py)
    ]
    if settings.keep_active:
        args.append("--keep-active")      # --stay-awake only works while plugged in over USB
    if settings.turn_screen_off:
        args.append("--turn-screen-off")
    return args


@dataclass(frozen=True)
class MirrorOutcome:
    kind: str          # ok | disconnected | no_device | server | input_blocked | encoder | failed
    message: str
    retry_smaller: bool = False


_PATTERNS = [
    (re.compile(r"INJECT_EVENTS|injecting input", re.I), "input_blocked",
     "Your device blocked touch input from the Frame. On Xiaomi/Redmi/POCO turn on "
     "\"USB debugging (Security settings)\"; on OPPO/realme/OnePlus turn on "
     "\"Disable permission monitoring\" in Developer options.", False),
    (re.compile(r"CodecException|Encoding error|encoder\b.*\b(fail|error)|(fail|error)\w*\b.*\bencoder",
                re.I), "encoder",
     "Your device's video encoder couldn't handle these settings. Trying a smaller resolution "
     "usually fixes it.", True),
    (re.compile(r"Could not find any ADB device|device '[^']*' not found", re.I), "no_device",
     "The device isn't connected any more. Check that Wireless debugging is still on.", False),
    (re.compile(r"Server connection failed|Could not connect to|connection refused", re.I), "server",
     "scrcpy couldn't start on the device. Unlock the device and try again.", False),
]


def classify(exit_code: int, stderr: str) -> MirrorOutcome:
    if exit_code == EXIT_OK:
        return MirrorOutcome("ok", "")
    if exit_code == EXIT_DISCONNECTED:
        return MirrorOutcome("disconnected",
                             "The connection to your device dropped. It may have locked, left the "
                             "Wi-Fi or turned off Wireless debugging.")
    for pattern, kind, message, retry in _PATTERNS:
        if pattern.search(stderr):
            return MirrorOutcome(kind, message, retry)
    tail = "\n".join(line for line in stderr.strip().splitlines()[-3:])
    return MirrorOutcome("failed", "Couldn't connect to the device." + (f"\n{tail}" if tail else ""))
