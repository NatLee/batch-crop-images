from __future__ import annotations

import pytest

from batch_crop.geometry import Size
from batch_crop.viewport import MAX_PIXEL_SCALE, Viewport


def test_big_pictures_fit_the_view() -> None:
    # Issue #1: a picture bigger than the screen must still be shown whole.
    viewport = Viewport(Size(4000, 3000), Size(800, 500))
    assert viewport.scale == pytest.approx(500 / 3000)
    left, top = viewport.to_view(0, 0)
    right, bottom = viewport.to_view(4000, 3000)
    assert left >= 0 and top == pytest.approx(0) and right <= 800 and bottom == pytest.approx(500)


def test_view_and_image_coordinates_are_inverse() -> None:
    viewport = Viewport(Size(1280, 720), Size(640, 480))
    viewport.zoom_by(3, around=(100, 200))
    for point in ((0.0, 0.0), (12.5, 600.25), (1280.0, 720.0)):
        assert viewport.to_image(*viewport.to_view(*point)) == pytest.approx(point)


def test_small_picture_is_centred() -> None:
    viewport = Viewport(Size(400, 300), Size(640, 360))
    assert viewport.scale == pytest.approx(1.2)
    assert viewport.to_view(0, 0) == pytest.approx((80, 0))


def test_zoom_keeps_the_point_under_the_mouse() -> None:
    viewport = Viewport(Size(1000, 1000), Size(500, 500))
    before = viewport.to_image(120, 340)
    viewport.zoom_by(2.0, around=(120, 340))
    assert viewport.zoom == 2.0
    assert viewport.to_image(120, 340) == pytest.approx(before)


def test_zoom_limits() -> None:
    viewport = Viewport(Size(100, 100), Size(500, 500))
    viewport.zoom_by(0.1)
    assert viewport.zoom == 1.0  # never smaller than "fit"
    viewport.zoom_by(1000)
    assert viewport.scale == pytest.approx(MAX_PIXEL_SCALE)
    viewport.reset()
    assert viewport.zoom == 1.0


def test_pan_stays_on_the_picture() -> None:
    viewport = Viewport(Size(1000, 1000), Size(500, 500))
    viewport.zoom_by(2.0)
    viewport.pan(10_000, 10_000)
    assert viewport.origin == pytest.approx((0, 0))
    viewport.pan(-10_000, -10_000)
    assert viewport.origin == pytest.approx((500, 500))


def test_resize_keeps_the_whole_picture_at_zoom_1() -> None:
    viewport = Viewport(Size(1000, 500), Size(500, 250))
    viewport.resize(Size(1000, 500))
    assert viewport.scale == pytest.approx(1.0)
    assert viewport.origin == pytest.approx((0, 0))


def test_affine_lines_pixel_centres_up_with_box_edges() -> None:
    viewport = Viewport(Size(100, 100), Size(400, 400))  # 4 view pixels per image pixel
    (a, _, tx), (_, d, ty) = viewport.affine()
    assert (a, d) == (4.0, 4.0)
    # Image pixel 0 covers view pixels 0..3, so its centre (index 0) lands on view index 1.5.
    assert tx == pytest.approx(1.5) and ty == pytest.approx(1.5)
    (a_half, _, _), _ = viewport.affine(source_scale=0.5)
    assert a_half == 8.0
