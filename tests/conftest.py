"""Shared helpers: test pictures whose pixels say where they came from."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from PIL import Image


def coded_image(width: int, height: int, mode: str = "RGB") -> Image.Image:
    """A picture whose red channel is x % 256 and green channel is y % 256.

    Reading a pixel of a cropped copy tells exactly where the crop started.
    """
    ys, xs = np.indices((height, width))
    rgb = np.dstack([xs % 256, ys % 256, (xs // 256 + ys // 256) * 40 % 256]).astype(np.uint8)
    image = Image.fromarray(rgb, "RGB")
    return image if mode == "RGB" else image.convert(mode)


def origin_of(image: Image.Image) -> tuple[int, int]:
    """The (x, y) where a crop of a :func:`coded_image` started (valid for crops starting below 256)."""
    red, green = image.convert("RGB").getpixel((0, 0))[:2]
    return int(red), int(green)


MakeImage = Callable[..., Path]


@pytest.fixture
def make_image(tmp_path: Path) -> MakeImage:
    """Write a coded test picture: ``make_image("in/a.png", 400, 300)`` returns its path."""

    def make(relative: str, width: int, height: int, mode: str = "RGB", **save_params: object) -> Path:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        coded_image(width, height, mode).save(path, **save_params)
        return path

    return make
