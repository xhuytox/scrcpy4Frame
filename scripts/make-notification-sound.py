#!/usr/bin/env python3
"""Generate src/scrcpy4frame/content/sounds/notification.wav, a short two-note chime.

Synthesised with the standard library so the sound has no licensing questions. Rerun and commit
the .wav after changing the parameters.
"""

import math
import struct
import wave
from pathlib import Path

RATE = 48000                 # PipeWire's native rate on the Steam Frame
LENGTH_S = 0.55
PEAK = 0.45                  # of full scale (about -7 dBFS)
# (start s, frequency Hz, decay time constant s, level)
NOTES = [(0.00, 880.0, 0.12, 0.8),      # A5
         (0.09, 1318.5, 0.18, 1.0)]     # E6
ATTACK_S = 0.006

OUT = Path(__file__).resolve().parent.parent / "src/scrcpy4frame/content/sounds/notification.wav"


def sample(t: float) -> float:
    total = 0.0
    for start, freq, decay, level in NOTES:
        u = t - start
        if u < 0:
            continue
        envelope = min(1.0, u / ATTACK_S) * math.exp(-u / decay)
        # A touch of the octave makes it bell-like rather than a pure beep.
        total += level * envelope * (math.sin(2 * math.pi * freq * u) + 0.18 * math.sin(4 * math.pi * freq * u))
    return total


def main() -> None:
    raw = [sample(i / RATE) for i in range(int(LENGTH_S * RATE))]
    fade = int(0.02 * RATE)                       # no click at the end
    for i in range(fade):
        raw[-1 - i] *= i / fade
    scale = PEAK / max(abs(v) for v in raw)
    frames = b"".join(struct.pack("<h", round(v * scale * 32767)) for v in raw)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUT), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(frames)
    print(f"wrote {OUT} ({len(frames)} bytes of audio)")


if __name__ == "__main__":
    main()
