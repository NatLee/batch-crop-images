from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from batch_crop.engine import CropSpec
from batch_crop.geometry import Box, Size
from batch_crop.preset import PRESET_FORMAT, PresetError, from_dict, load_preset, save_preset, to_dict

SPEC = CropSpec(Box(120, 40, 558, 332), Size(1280, 720))


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "box.json"
    save_preset(path, SPEC)
    assert load_preset(path) == SPEC
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "format": "batch-crop-preset",
        "version": 1,
        "box": {"x": 120, "y": 40, "width": 558, "height": 332},
        "reference_size": {"width": 1280, "height": 720},
    }


def _with(**changes: Any) -> dict[str, Any]:
    data = to_dict(SPEC)
    data.update(changes)
    return data


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([], "not a batch-crop preset"),
        ({"box": {}}, "not a batch-crop preset"),
        (_with(version=2), "version 2 is not supported"),
        (_with(box={"x": 1, "y": 2, "width": 3}), "missing a field"),
        (_with(box={"x": 1.5, "y": 2, "width": 3, "height": 4}), "wrong type"),
        (_with(box={"x": True, "y": 2, "width": 3, "height": 4}), "wrong type"),
        (_with(box={"x": 1200, "y": 0, "width": 558, "height": 332}), "does not fit"),
        (_with(box={"x": 0, "y": 0, "width": 0, "height": 332}), "not valid"),
        (_with(reference_size={"width": "1280", "height": 720}), "wrong type"),
    ],
)
def test_bad_presets_are_explained(data: Any, message: str) -> None:
    with pytest.raises(PresetError, match=message):
        from_dict(data)


def test_unreadable_files(tmp_path: Path) -> None:
    with pytest.raises(PresetError, match="cannot read preset"):
        load_preset(tmp_path / "missing.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{nope", encoding="utf-8")
    with pytest.raises(PresetError, match="not valid JSON"):
        load_preset(broken)


def test_format_name_is_stable() -> None:
    assert PRESET_FORMAT == "batch-crop-preset"
