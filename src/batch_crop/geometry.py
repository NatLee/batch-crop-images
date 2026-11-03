"""Sizes, crop boxes, aspect ratios and anchors.

Everything here is plain arithmetic, so the crop engine, the preset files, the command line and the
interactive window all describe a crop the same way.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import NamedTuple


class Size(NamedTuple):
    """Width and height of an image in pixels."""

    width: int
    height: int

    def __str__(self) -> str:
        return f"{self.width}x{self.height}"


def round_half_up(value: float) -> int:
    """Round to the nearest integer, halves away from zero (Python's ``round`` rounds halves to even)."""
    return math.floor(value + 0.5) if value >= 0 else -math.floor(-value + 0.5)


# Separators accepted between the numbers of a box: "10,20,300,200", "10 20 300 200", "10, 20, 300x200".
_BOX_SEPARATORS = re.compile(r"[\s,;x]+")


@dataclass(frozen=True)
class Box:
    """A crop rectangle in pixels: the top-left corner plus a width and a height (both at least 1)."""

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width < 1 or self.height < 1:
            raise ValueError(f"a crop box needs a width and height of at least 1 px, got {self.width}x{self.height}")
        if self.x < 0 or self.y < 0:
            raise ValueError(f"a crop box cannot start at a negative position, got x={self.x}, y={self.y}")

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def size(self) -> Size:
        return Size(self.width, self.height)

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.width / 2, self.y + self.height / 2

    @classmethod
    def from_corners(cls, x0: float, y0: float, x1: float, y1: float) -> Box:
        """Build a box from two opposite corners given in any order.

        Dragging from bottom-right to top-left is as valid as the other way round; the original tool
        produced an empty crop (and crashed while saving it) in that case, see GitHub issue #2.
        """
        left, right = sorted((round_half_up(x0), round_half_up(x1)))
        top, bottom = sorted((round_half_up(y0), round_half_up(y1)))
        return cls(left, top, right - left, bottom - top)

    @classmethod
    def parse(cls, text: str) -> Box:
        """Parse ``"X,Y,W,H"`` (commas, spaces or an ``x`` between the numbers)."""
        parts = [part for part in _BOX_SEPARATORS.split(text.strip()) if part]
        if len(parts) != 4 or not all(part.isdigit() for part in parts):
            raise ValueError(f"expected four whole numbers X,Y,W,H (for example 120,40,558,332), got {text!r}")
        x, y, width, height = (int(part) for part in parts)
        return cls(x, y, width, height)

    def __str__(self) -> str:
        """The same ``X,Y,W,H`` text that :meth:`parse` and ``--box`` accept."""
        return f"{self.x},{self.y},{self.width},{self.height}"

    def fits_in(self, size: Size) -> bool:
        return self.right <= size.width and self.bottom <= size.height

    def moved_inside(self, size: Size) -> Box:
        """Shift the box so it lies inside ``size``; shrink it only when it is larger than ``size``."""
        width, height = min(self.width, size.width), min(self.height, size.height)
        x = min(max(self.x, 0), size.width - width)
        y = min(max(self.y, 0), size.height - height)
        return Box(x, y, width, height)


@dataclass(frozen=True)
class AspectRatio:
    """A width:height ratio such as 16:9, used to lock the shape of the box in the window."""

    width: float
    height: float

    def __post_init__(self) -> None:
        if not (self.width > 0 and self.height > 0 and math.isfinite(self.width) and math.isfinite(self.height)):
            raise ValueError(f"an aspect ratio needs two positive numbers, got {self.width}:{self.height}")

    @property
    def value(self) -> float:
        return self.width / self.height

    @classmethod
    def parse(cls, text: str) -> AspectRatio | None:
        """Parse ``"16:9"``, ``"16/9"`` or ``"1.5"``; ``"free"`` means no lock and returns ``None``."""
        cleaned = text.strip().lower()
        if cleaned in FREE_ASPECT_WORDS:
            return None
        numbers = re.split(r"[:/]", cleaned)
        try:
            if len(numbers) == 2:
                return cls(float(numbers[0]), float(numbers[1]))
            if len(numbers) == 1:
                return cls(float(numbers[0]), 1.0)
        except ValueError:
            pass
        raise ValueError(f"expected a ratio such as 16:9, 1:1 or 1.5 (or 'free'), got {text!r}")

    def __str__(self) -> str:
        def number(value: float) -> str:
            return str(int(value)) if value.is_integer() else f"{value:g}"

        return f"{number(self.width)}:{number(self.height)}"


# Words that mean "no aspect-ratio lock" on the command line.
FREE_ASPECT_WORDS = ("free", "none", "off", "")

# Ratios the window cycles through with the R key; None is the unlocked state.
ASPECT_PRESETS: tuple[AspectRatio | None, ...] = (
    None,
    AspectRatio(1, 1),
    AspectRatio(4, 3),
    AspectRatio(3, 2),
    AspectRatio(16, 9),
    AspectRatio(3, 4),
    AspectRatio(2, 3),
    AspectRatio(9, 16),
)


class Anchor(Enum):
    """Which part of an image a fixed-size box stays attached to when images differ in size."""

    TOP_LEFT = "top-left"
    TOP = "top"
    TOP_RIGHT = "top-right"
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"
    BOTTOM_LEFT = "bottom-left"
    BOTTOM = "bottom"
    BOTTOM_RIGHT = "bottom-right"

    @property
    def fractions(self) -> tuple[float, float]:
        """How far along each axis the anchor sits: 0 is the left/top edge, 1 the right/bottom edge."""
        words = self.value.split("-")
        fx = 0.0 if "left" in words else 1.0 if "right" in words else 0.5
        fy = 0.0 if "top" in words else 1.0 if "bottom" in words else 0.5
        return fx, fy
