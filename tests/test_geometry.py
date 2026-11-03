from __future__ import annotations

import pytest

from batch_crop.geometry import ASPECT_PRESETS, Anchor, AspectRatio, Box, Size, round_half_up


class TestBox:
    def test_corners_in_any_order_give_the_same_box(self) -> None:
        expected = Box(100, 80, 400, 300)
        for corners in ((100, 80, 500, 380), (500, 380, 100, 80), (500, 80, 100, 380), (100, 380, 500, 80)):
            assert Box.from_corners(*corners) == expected

    def test_issue_2_right_to_left_drag_is_not_empty(self) -> None:
        # The coordinates printed in GitHub issue #2: dragging leftwards used to give an empty crop.
        box = Box.from_corners(1008, 6, 692, 257)
        assert box == Box(692, 6, 316, 251)

    def test_fractional_corners_round_half_up(self) -> None:
        assert Box.from_corners(0.5, 1.49, 10.5, 11.5) == Box(1, 1, 10, 11)

    @pytest.mark.parametrize("text", ["120,40,558,332", "120 40 558 332", " 120, 40, 558x332 ", "120;40;558;332"])
    def test_parse_accepts_common_separators(self, text: str) -> None:
        assert Box.parse(text) == Box(120, 40, 558, 332)

    @pytest.mark.parametrize("text", ["", "1,2,3", "1,2,3,4,5", "a,b,c,d", "1.5,2,3,4", "-1,2,3,4"])
    def test_parse_rejects_anything_else(self, text: str) -> None:
        with pytest.raises(ValueError, match="X,Y,W,H"):
            Box.parse(text)

    def test_str_round_trips_through_parse(self) -> None:
        box = Box(7, 8, 9, 10)
        assert Box.parse(str(box)) == box

    @pytest.mark.parametrize("args", [(0, 0, 0, 5), (0, 0, 5, 0), (-1, 0, 5, 5), (0, -1, 5, 5)])
    def test_empty_or_negative_boxes_are_refused(self, args: tuple[int, int, int, int]) -> None:
        with pytest.raises(ValueError):
            Box(*args)

    def test_edges_and_fit(self) -> None:
        box = Box(10, 20, 30, 40)
        assert (box.right, box.bottom, box.size, box.center) == (40, 60, Size(30, 40), (25.0, 40.0))
        assert box.fits_in(Size(40, 60))
        assert not box.fits_in(Size(39, 60))

    def test_moved_inside_keeps_size_when_possible(self) -> None:
        assert Box(90, 90, 20, 20).moved_inside(Size(100, 100)) == Box(80, 80, 20, 20)
        assert Box(0, 0, 300, 50).moved_inside(Size(100, 100)) == Box(0, 0, 100, 50)


class TestAspectRatio:
    @pytest.mark.parametrize(("text", "value"), [("16:9", 16 / 9), ("16/9", 16 / 9), ("1.5", 1.5), ("1:1", 1.0)])
    def test_parse(self, text: str, value: float) -> None:
        ratio = AspectRatio.parse(text)
        assert ratio is not None
        assert ratio.value == pytest.approx(value)

    @pytest.mark.parametrize("text", ["free", "FREE", "none", "off", ""])
    def test_free_means_no_lock(self, text: str) -> None:
        assert AspectRatio.parse(text) is None

    @pytest.mark.parametrize("text", ["wide", "16:", "0:9", "16:9:1", "-4:3", "inf"])
    def test_parse_rejects_nonsense(self, text: str) -> None:
        with pytest.raises(ValueError):
            AspectRatio.parse(text)

    def test_str(self) -> None:
        assert str(AspectRatio(16, 9)) == "16:9"
        assert str(AspectRatio(2.35, 1)) == "2.35:1"

    def test_presets_start_unlocked(self) -> None:
        assert ASPECT_PRESETS[0] is None
        assert AspectRatio(16, 9) in ASPECT_PRESETS


@pytest.mark.parametrize(
    ("anchor", "fractions"),
    [
        (Anchor.TOP_LEFT, (0.0, 0.0)),
        (Anchor.TOP, (0.5, 0.0)),
        (Anchor.CENTER, (0.5, 0.5)),
        (Anchor.RIGHT, (1.0, 0.5)),
        (Anchor.BOTTOM_RIGHT, (1.0, 1.0)),
        (Anchor.BOTTOM_LEFT, (0.0, 1.0)),
    ],
)
def test_anchor_fractions(anchor: Anchor, fractions: tuple[float, float]) -> None:
    assert anchor.fractions == fractions


@pytest.mark.parametrize(("value", "expected"), [(0.5, 1), (1.5, 2), (2.5, 3), (2.49, 2), (-0.5, -1), (-1.4, -1)])
def test_round_half_up(value: float, expected: int) -> None:
    assert round_half_up(value) == expected
