"""Brand-specific instructions for each phone-side step (content/guides.json)."""

from __future__ import annotations

import json
import re
from functools import lru_cache

from . import paths


@lru_cache(maxsize=1)
def _data() -> dict:
    return json.loads((paths.content_dir() / "guides.json").read_text())


def brands() -> list[dict]:
    return list(_data()["brands"])


def text(name: str, brand: str) -> str:
    """One brand-specific phrase from the guides' vars."""
    options = _data()["vars"][name]
    return options.get(brand, options["other"])


def guide(step: str, brand: str, ssid: str = "") -> dict:
    data = _data()
    entry = data["steps"].get(step)
    if entry is None:
        return {"title": "", "lines": [], "tip": "", "help": [], "command": ""}
    brand = brand if brand in {b["id"] for b in data["brands"]} else "other"
    values = {name: options.get(brand, options["other"]) for name, options in data["vars"].items()}
    values["ssid"] = f"\"{ssid}\"" if ssid else "your home Wi-Fi"
    if "oemInputLine" in entry:
        options = entry["oemInputLine"]
        values["oemInputLine"] = options.get(brand, options["other"])

    def fill(text: str) -> str:
        # Two passes: substituted values may themselves contain {devOptions}.
        for _ in range(2):
            text = re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), text)
        return text

    return {
        "title": fill(entry.get("title", "")),
        "lines": [fill(line) for line in entry.get("lines", [])],
        "tip": fill(entry.get("tip", "")),
        "help": [fill(line) for line in entry.get("help", [])],
        "command": entry.get("command", ""),
    }
