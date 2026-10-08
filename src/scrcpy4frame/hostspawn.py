"""Run native OpenVR helpers (vr-toast, vr-mirror) on the host when inside the Flatpak.

OpenVR rejects sandboxed clients: the PID namespace makes them look like PID 2 to SteamVR.
"""

from __future__ import annotations

import filecmp
import shutil
from pathlib import Path

from . import paths


def _sync(source: Path, target: Path, executable: bool) -> None:
    if not target.exists() or not filecmp.cmp(source, target, shallow=False):
        shutil.copy2(source, target)
    if executable:
        target.chmod(0o755)


def command(binary: str, companions: list[str | Path] = ()) -> tuple[str, list[str]]:
    """(program, arguments) that run `binary` where it can reach SteamVR.

    --watch-bus makes the host process exit with us. Without it a crashed app leaves the helper
    running, since the portal keeps its stdin open.
    """
    if not paths.in_flatpak():
        return binary, []
    # The host can't see /app, but the data dir has the same path on both sides.
    target = paths.data_dir() / Path(binary).name
    _sync(Path(binary), target, executable=True)
    for companion in companions:
        if companion and Path(companion).is_file():
            _sync(Path(companion), paths.data_dir() / Path(companion).name, executable=False)
    return "flatpak-spawn", ["--host", "--watch-bus", str(target)]
