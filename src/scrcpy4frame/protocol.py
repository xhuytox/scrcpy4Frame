"""JSON-lines framing for the companion socket (see docs/CONTRACTS.md)."""

from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

MAX_LINE = 1 << 20
PROTOCOL_VERSION = 2


def encode(message: dict) -> bytes:
    return (json.dumps(message, separators=(",", ":")) + "\n").encode()


class LineBuffer:
    """Accumulates socket bytes and yields decoded JSON objects, one per complete line."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._discarding = False

    def feed(self, data: bytes) -> list[dict]:
        self._buf.extend(data)
        messages = []
        while True:
            newline = self._buf.find(b"\n")
            if newline < 0:
                if len(self._buf) > MAX_LINE:     # oversize line: drop until the next newline
                    self._buf.clear()
                    self._discarding = True
                break
            line = bytes(self._buf[:newline])
            del self._buf[: newline + 1]
            if self._discarding:
                self._discarding = False
                continue
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except ValueError:
                log.warning("dropping malformed companion line")
                continue
            if isinstance(message, dict) and isinstance(message.get("type"), str):
                messages.append(message)
        return messages
