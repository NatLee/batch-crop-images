"""Which part of the image the window shows, and how window pixels map to image pixels.

At zoom 1 the whole image fits the window (big photos are shown smaller, see GitHub issue #1).
Zooming in keeps the image point under the mouse where it is; panning moves the visible part.
"""

from __future__ import annotations

from .geometry import Size

# Limits for zooming in, as a multiple of the fit-to-window scale and as screen pixels per image pixel.
MAX_ZOOM = 64.0
MAX_PIXEL_SCALE = 32.0


class Viewport:
    def __init__(self, image: Size, view: Size) -> None:
        self.image = image
        self.view = view
        self.zoom = 1.0
        # The image point shown in the middle of the view.
        self._cx = image.width / 2
        self._cy = image.height / 2

    @property
    def fit_scale(self) -> float:
        return min(self.view.width / self.image.width, self.view.height / self.image.height)

    @property
    def scale(self) -> float:
        """Screen pixels per image pixel."""
        return self.fit_scale * self.zoom

    @property
    def origin(self) -> tuple[float, float]:
        """The image point at the top-left corner of the view."""
        scale = self.scale
        return self._cx - self.view.width / (2 * scale), self._cy - self.view.height / (2 * scale)

    def to_image(self, vx: float, vy: float) -> tuple[float, float]:
        ox, oy = self.origin
        return ox + vx / self.scale, oy + vy / self.scale

    def to_view(self, ix: float, iy: float) -> tuple[float, float]:
        ox, oy = self.origin
        return (ix - ox) * self.scale, (iy - oy) * self.scale

    def resize(self, view: Size) -> None:
        self.view = view
        self._clamp()

    def zoom_by(self, factor: float, around: tuple[float, float] | None = None) -> None:
        """Zoom by ``factor``; the image point under ``around`` (view pixels) stays put."""
        vx, vy = around if around is not None else (self.view.width / 2, self.view.height / 2)
        ix, iy = self.to_image(vx, vy)
        max_zoom = min(MAX_ZOOM, MAX_PIXEL_SCALE / self.fit_scale) if self.fit_scale else MAX_ZOOM
        self.zoom = min(max(self.zoom * factor, 1.0), max(max_zoom, 1.0))
        scale = self.scale
        self._cx = ix - vx / scale + self.view.width / (2 * scale)
        self._cy = iy - vy / scale + self.view.height / (2 * scale)
        self._clamp()

    def reset(self) -> None:
        self.zoom = 1.0
        self._clamp()

    def pan(self, dvx: float, dvy: float) -> None:
        """Move the picture by (dvx, dvy) view pixels, like dragging it."""
        self._cx -= dvx / self.scale
        self._cy -= dvy / self.scale
        self._clamp()

    def _clamp(self) -> None:
        """Centre the image on an axis where it is smaller than the view; else keep the view on it."""
        scale = self.scale
        half_w, half_h = self.view.width / (2 * scale), self.view.height / (2 * scale)
        self._cx = _clamp_centre(self._cx, half_w, self.image.width)
        self._cy = _clamp_centre(self._cy, half_h, self.image.height)

    def affine(self, source_scale: float = 1.0) -> list[list[float]]:
        """The 2x3 matrix taking pixel indices of a copy scaled by ``source_scale`` to view pixel indices.

        Pixel *indices* name pixel centres, which sit half a pixel inside the continuous coordinates
        used everywhere else; the 0.5 terms keep zoomed-in pixels lined up with the box edges.
        """
        ox, oy = self.origin
        step = self.scale / source_scale
        return [
            [step, 0.0, 0.5 * step - ox * self.scale - 0.5],
            [0.0, step, 0.5 * step - oy * self.scale - 0.5],
        ]


def _clamp_centre(centre: float, half_view: float, length: int) -> float:
    if 2 * half_view >= length:
        return length / 2
    return min(max(centre, half_view), length - half_view)
