"""What mouse gestures and keys do to the crop box, without any window.

All coordinates are image pixels; they are fractional while the mouse moves and get rounded when a
:class:`~batch_crop.geometry.Box` is built. The window (``window.py``) only translates screen events
into these calls, so every gesture can be tested without a display.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from .geometry import ASPECT_PRESETS, AspectRatio, Box, Size, round_half_up


class Handle(Enum):
    """The part of the box a mouse press grabbed."""

    NEW = "new"  # outside the box: draw a new one
    MOVE = "move"  # inside the box: move it
    TOP_LEFT = (-1, -1)
    TOP = (0, -1)
    TOP_RIGHT = (1, -1)
    LEFT = (-1, 0)
    RIGHT = (1, 0)
    BOTTOM_LEFT = (-1, 1)
    BOTTOM = (0, 1)
    BOTTOM_RIGHT = (1, 1)

    @property
    def sides(self) -> tuple[int, int]:
        """Which edges this handle moves: -1 is left/top, 1 is right/bottom, 0 is neither."""
        return self.value if isinstance(self.value, tuple) else (0, 0)


class NudgeTarget(Enum):
    """What the arrow keys move."""

    BOX = "box"
    TOP_LEFT = "top-left corner"
    BOTTOM_RIGHT = "bottom-right corner"

    @property
    def handle(self) -> Handle:
        return _TARGET_HANDLES[self]

    def next(self) -> NudgeTarget:
        members = list(NudgeTarget)
        return members[(members.index(self) + 1) % len(members)]


_TARGET_HANDLES = {
    NudgeTarget.BOX: Handle.MOVE,
    NudgeTarget.TOP_LEFT: Handle.TOP_LEFT,
    NudgeTarget.BOTTOM_RIGHT: Handle.BOTTOM_RIGHT,
}


@dataclass(frozen=True)
class _Drag:
    handle: Handle
    start_box: Box | None
    x: float
    y: float


class Selection:
    """The crop box being edited on an image of ``bounds`` size, plus the aspect-ratio lock."""

    def __init__(self, bounds: Size, box: Box | None = None, aspect: AspectRatio | None = None) -> None:
        self.bounds = bounds
        self.box: Box | None = None
        self.aspect = aspect
        self.target = NudgeTarget.BOX
        self._drag: _Drag | None = None
        if box is not None:
            self.set_box(box)

    # ----- mouse -------------------------------------------------------------------------------

    def hit_test(self, x: float, y: float, tolerance: float) -> Handle:
        """Which handle a press at (x, y) grabs; ``tolerance`` is how close to an edge counts."""
        box = self.box
        if box is None:
            return Handle.NEW
        inside_x = box.x - tolerance <= x <= box.right + tolerance
        inside_y = box.y - tolerance <= y <= box.bottom + tolerance
        hx = _nearest_side(x, box.x, box.right, tolerance) if inside_y else 0
        hy = _nearest_side(y, box.y, box.bottom, tolerance) if inside_x else 0
        if hx or hy:
            return Handle((hx, hy))
        if box.x < x < box.right and box.y < y < box.bottom:
            return Handle.MOVE
        return Handle.NEW

    @property
    def dragging(self) -> bool:
        return self._drag is not None

    def begin(self, x: float, y: float, tolerance: float) -> Handle:
        handle = self.hit_test(x, y, tolerance)
        px, py = self._clamp(x, y)
        self._drag = _Drag(handle, self.box, px, py)
        return handle

    def update(self, x: float, y: float) -> None:
        drag = self._drag
        if drag is None:
            return
        box = self._dragged(drag, x, y)
        if box is not None:
            self.box = box

    def end(self, x: float, y: float) -> None:
        self.update(x, y)
        self._drag = None

    def _dragged(self, drag: _Drag, x: float, y: float) -> Box | None:
        start = drag.start_box
        if drag.handle is Handle.NEW or start is None:
            return self._from_fixed_corner((drag.x, drag.y), self._clamp(x, y), driver=None)
        if drag.handle is Handle.MOVE:
            return self._moved(start, x - drag.x, y - drag.y)
        return self._resized(start, drag.handle, self._clamp(x, y), driver=None)

    # ----- keyboard ----------------------------------------------------------------------------

    def nudge(self, dx: int, dy: int) -> None:
        """Move whatever :attr:`target` points at by (dx, dy) pixels."""
        box = self.box
        if box is None or (dx == 0 and dy == 0):
            return
        if self.target is NudgeTarget.BOX:
            self.box = self._moved(box, dx, dy)
            return
        handle = self.target.handle
        hx, hy = handle.sides
        corner = (box.right if hx > 0 else box.x) + dx, (box.bottom if hy > 0 else box.y) + dy
        resized = self._resized(box, handle, self._clamp(*corner), driver="x" if dx else "y")
        if resized is not None:
            self.box = resized

    def cycle_target(self) -> NudgeTarget:
        self.target = self.target.next()
        return self.target

    def select_all(self) -> None:
        self.box = Box(0, 0, self.bounds.width, self.bounds.height)
        if self.aspect is not None:
            self.set_aspect(self.aspect)

    def set_box(self, box: Box) -> bool:
        """Use an exact box (typed numbers). Returns True if that unlocked a ratio it does not match."""
        if not box.fits_in(self.bounds):
            raise ValueError(f"box {box} does not fit in the {self.bounds} image")
        self.box = box
        if self.aspect is not None and abs(box.width - box.height * self.aspect.value) >= 1:
            self.aspect = None
            return True
        return False

    def set_aspect(self, aspect: AspectRatio | None) -> None:
        """Lock (or unlock) the ratio; a current box is reshaped around its centre, keeping its area."""
        self.aspect = aspect
        box = self.box
        if aspect is None or box is None:
            return
        width = math.sqrt(box.width * box.height * aspect.value)
        height = width / aspect.value
        shrink = min(1.0, self.bounds.width / width, self.bounds.height / height)
        width, height = width * shrink, height * shrink
        cx, cy = box.center
        x0 = min(max(cx - width / 2, 0.0), self.bounds.width - width)
        y0 = min(max(cy - height / 2, 0.0), self.bounds.height - height)
        reshaped = _box_or_none(x0, y0, x0 + width, y0 + height)
        if reshaped is not None:
            self.box = reshaped

    def cycle_aspect(self) -> AspectRatio | None:
        """Switch to the next ratio in :data:`~batch_crop.geometry.ASPECT_PRESETS`."""
        index = ASPECT_PRESETS.index(self.aspect) if self.aspect in ASPECT_PRESETS else 0
        self.set_aspect(ASPECT_PRESETS[(index + 1) % len(ASPECT_PRESETS)])
        return self.aspect

    # ----- geometry ----------------------------------------------------------------------------

    def _clamp(self, x: float, y: float) -> tuple[float, float]:
        return min(max(x, 0.0), float(self.bounds.width)), min(max(y, 0.0), float(self.bounds.height))

    def _moved(self, box: Box, dx: float, dy: float) -> Box:
        x = min(max(box.x + dx, 0.0), self.bounds.width - box.width)
        y = min(max(box.y + dy, 0.0), self.bounds.height - box.height)
        return Box(round_half_up(x), round_half_up(y), box.width, box.height)

    def _resized(self, start: Box, handle: Handle, point: tuple[float, float], driver: str | None) -> Box | None:
        hx, hy = handle.sides
        px, py = point
        if hx and hy:  # a corner: the opposite corner stays where it is
            fixed = (start.x if hx > 0 else start.right, start.y if hy > 0 else start.bottom)
            return self._from_fixed_corner(fixed, point, driver)
        if hx:  # the left or right edge
            return self._from_edge(start, along_x=True, fixed=start.x if hx > 0 else start.right, moving=px)
        return self._from_edge(start, along_x=False, fixed=start.y if hy > 0 else start.bottom, moving=py)

    def _from_fixed_corner(
        self, fixed: tuple[float, float], moving: tuple[float, float], driver: str | None
    ) -> Box | None:
        """The box between a fixed corner and the pointer, reshaped to the locked ratio if any.

        With a ratio, ``driver`` says which side follows the pointer ("x" or "y"); ``None`` takes
        whichever gives the bigger box. The box then shrinks to the room left in the image.
        """
        (fx, fy), (mx, my) = fixed, moving
        width, height = abs(mx - fx), abs(my - fy)
        sign_x, sign_y = (1 if mx >= fx else -1), (1 if my >= fy else -1)
        if self.aspect is not None:
            ratio = self.aspect.value
            if driver == "x":
                height = width / ratio
            elif driver == "y":
                width = height * ratio
            else:
                width = max(width, height * ratio)
                height = width / ratio
            room_x = self.bounds.width - fx if sign_x > 0 else fx
            room_y = self.bounds.height - fy if sign_y > 0 else fy
            shrink = min(1.0, room_x / width if width else 1.0, room_y / height if height else 1.0)
            width, height = width * shrink, height * shrink
        return _box_or_none(fx, fy, fx + sign_x * width, fy + sign_y * height)

    def _from_edge(self, start: Box, *, along_x: bool, fixed: float, moving: float) -> Box | None:
        """Drag one edge; with a ratio lock the other side follows, centred on the old box."""
        cross_lo, cross_hi = (float(start.y), float(start.bottom)) if along_x else (float(start.x), float(start.right))
        if self.aspect is not None:
            ratio = self.aspect.value if along_x else 1 / self.aspect.value
            length = abs(moving - fixed)
            centre = (cross_lo + cross_hi) / 2
            cross_limit = self.bounds.height if along_x else self.bounds.width
            cross = min(length / ratio, 2 * min(centre, cross_limit - centre))
            moving = fixed + math.copysign(cross * ratio, moving - fixed)
            cross_lo, cross_hi = centre - cross / 2, centre + cross / 2
        if along_x:
            return _box_or_none(fixed, cross_lo, moving, cross_hi)
        return _box_or_none(cross_lo, fixed, cross_hi, moving)


def _nearest_side(value: float, low: float, high: float, tolerance: float) -> int:
    """-1 if ``value`` is within ``tolerance`` of ``low``, 1 if of ``high`` (the closer wins), else 0."""
    to_low, to_high = abs(value - low), abs(value - high)
    if min(to_low, to_high) > tolerance:
        return 0
    return -1 if to_low <= to_high else 1


def _box_or_none(x0: float, y0: float, x1: float, y1: float) -> Box | None:
    """A box from two corners, or None while it is still smaller than one pixel."""
    try:
        return Box.from_corners(x0, y0, x1, y1)
    except ValueError:
        return None
