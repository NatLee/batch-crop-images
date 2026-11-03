"""Preset files: a crop box saved as JSON so the same batch can be re-run without the window.

A preset stores the box and the size of the image it was drawn on, which is what ``--mismatch``
needs to adapt the box to images of other sizes::

    {
      "format": "batch-crop-preset",
      "version": 1,
      "box": {"x": 120, "y": 40, "width": 558, "height": 332},
      "reference_size": {"width": 1280, "height": 720}
    }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .engine import CropError, CropSpec
from .geometry import Box, Size

PRESET_FORMAT = "batch-crop-preset"
PRESET_VERSION = 1


class PresetError(ValueError):
    """A preset file is missing, unreadable or not a batch-crop preset."""


def to_dict(spec: CropSpec) -> dict[str, Any]:
    return {
        "format": PRESET_FORMAT,
        "version": PRESET_VERSION,
        "box": {"x": spec.box.x, "y": spec.box.y, "width": spec.box.width, "height": spec.box.height},
        "reference_size": {"width": spec.reference.width, "height": spec.reference.height},
    }


def from_dict(data: Any) -> CropSpec:
    if not isinstance(data, dict) or data.get("format") != PRESET_FORMAT:
        raise PresetError(f'not a batch-crop preset (expected "format": "{PRESET_FORMAT}")')
    if data.get("version") != PRESET_VERSION:
        raise PresetError(f"preset version {data.get('version')!r} is not supported (this tool reads {PRESET_VERSION})")
    try:
        box, ref = data["box"], data["reference_size"]
        return CropSpec(
            Box(_whole(box["x"]), _whole(box["y"]), _whole(box["width"]), _whole(box["height"])),
            Size(_whole(ref["width"]), _whole(ref["height"])),
        )
    except (KeyError, TypeError) as exc:
        raise PresetError(f"preset is missing a field or has a wrong type: {exc}") from exc
    except (CropError, ValueError) as exc:
        raise PresetError(f"preset box is not valid: {exc}") from exc


def _whole(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"expected a whole number, got {value!r}")
    return value


def save_preset(path: Path, spec: CropSpec) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_dict(spec), indent=2) + "\n", encoding="utf-8")


def load_preset(path: Path) -> CropSpec:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PresetError(f"cannot read preset {path}: {exc.strerror or exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PresetError(f"preset {path} is not valid JSON: {exc}") from exc
    return from_dict(data)
