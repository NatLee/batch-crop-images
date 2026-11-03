from __future__ import annotations

import pytest

from batch_crop.geometry import ASPECT_PRESETS, AspectRatio, Box, Size
from batch_crop.selection import Handle, NudgeTarget, Selection

BOUNDS = Size(800, 600)
TOLERANCE = 5.0


def drag(selection: Selection, start: tuple[float, float], end: tuple[float, float]) -> Handle:
    handle = selection.begin(*start, TOLERANCE)
    selection.update((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
    selection.end(*end)
    return handle


class TestDrawing:
    @pytest.mark.parametrize(
        ("start", "end"),
        [((100, 80), (500, 380)), ((500, 380), (100, 80)), ((500, 80), (100, 380)), ((100, 380), (500, 80))],
    )
    def test_any_direction(self, start: tuple[int, int], end: tuple[int, int]) -> None:
        selection = Selection(BOUNDS)
        assert drag(selection, start, end) is Handle.NEW
        assert selection.box == Box(100, 80, 400, 300)

    def test_pointer_outside_the_picture_is_clamped(self) -> None:
        selection = Selection(BOUNDS)
        drag(selection, (700, 500), (1200, -50))
        assert selection.box == Box(700, 0, 100, 500)

    def test_a_click_does_not_replace_the_box(self) -> None:
        selection = Selection(BOUNDS, Box(10, 10, 50, 50))
        drag(selection, (400, 400), (400, 400))
        assert selection.box == Box(10, 10, 50, 50)

    def test_aspect_lock_while_drawing(self) -> None:
        selection = Selection(BOUNDS, aspect=AspectRatio(16, 9))
        drag(selection, (100, 100), (420, 120))
        assert selection.box == Box(100, 100, 320, 180)

    def test_aspect_lock_stops_at_the_picture_edge(self) -> None:
        selection = Selection(BOUNDS, aspect=AspectRatio(1, 1))
        drag(selection, (700, 100), (790, 500))
        assert selection.box == Box(700, 100, 100, 100)


class TestEditing:
    def test_drag_inside_moves_and_stays_in_the_picture(self) -> None:
        selection = Selection(BOUNDS, Box(100, 100, 200, 100))
        assert drag(selection, (150, 150), (850, 160)) is Handle.MOVE
        assert selection.box == Box(600, 110, 200, 100)

    @pytest.mark.parametrize(
        ("grab", "to", "expected"),
        [
            ((300, 200), (350, 260), Box(100, 100, 250, 160)),  # bottom-right corner
            ((100, 100), (50, 40), Box(50, 40, 250, 160)),  # top-left corner
            ((300, 150), (400, 150), Box(100, 100, 300, 100)),  # right edge
            ((200, 100), (200, 20), Box(100, 20, 200, 180)),  # top edge
            ((300, 200), (40, 30), Box(40, 30, 60, 70)),  # dragged past the opposite corner: flips
        ],
    )
    def test_resize_by_corners_and_edges(self, grab: tuple[int, int], to: tuple[int, int], expected: Box) -> None:
        selection = Selection(BOUNDS, Box(100, 100, 200, 100))
        drag(selection, grab, to)
        assert selection.box == expected

    def test_edge_with_aspect_lock_resizes_the_other_side_around_the_centre(self) -> None:
        selection = Selection(BOUNDS, Box(100, 100, 200, 100), aspect=AspectRatio(2, 1))
        drag(selection, (300, 150), (500, 150))
        assert selection.box == Box(100, 50, 400, 200)

    def test_hit_test_prefers_corners_then_edges_then_inside(self) -> None:
        selection = Selection(BOUNDS, Box(100, 100, 200, 100))
        assert selection.hit_test(103, 97, TOLERANCE) is Handle.TOP_LEFT
        assert selection.hit_test(200, 198, TOLERANCE) is Handle.BOTTOM
        assert selection.hit_test(302, 150, TOLERANCE) is Handle.RIGHT
        assert selection.hit_test(200, 150, TOLERANCE) is Handle.MOVE
        assert selection.hit_test(400, 150, TOLERANCE) is Handle.NEW
        assert Selection(BOUNDS).hit_test(200, 150, TOLERANCE) is Handle.NEW


class TestKeyboard:
    def test_nudge_box_and_corners(self) -> None:
        selection = Selection(BOUNDS, Box(100, 100, 200, 100))
        selection.nudge(1, 0)
        assert selection.box == Box(101, 100, 200, 100)
        assert selection.cycle_target() is NudgeTarget.TOP_LEFT
        selection.nudge(0, -10)
        assert selection.box == Box(101, 90, 200, 110)
        assert selection.cycle_target() is NudgeTarget.BOTTOM_RIGHT
        selection.nudge(-1, 0)
        assert selection.box == Box(101, 90, 199, 110)
        assert selection.cycle_target() is NudgeTarget.BOX

    def test_nudge_cannot_leave_the_picture(self) -> None:
        selection = Selection(BOUNDS, Box(0, 0, 100, 100))
        selection.nudge(-50, -50)
        assert selection.box == Box(0, 0, 100, 100)

    def test_nudge_without_a_box_does_nothing(self) -> None:
        selection = Selection(BOUNDS)
        selection.nudge(1, 1)
        assert selection.box is None

    def test_nudging_a_corner_keeps_the_locked_ratio(self) -> None:
        selection = Selection(BOUNDS, Box(100, 100, 160, 90), aspect=AspectRatio(16, 9))
        selection.target = NudgeTarget.BOTTOM_RIGHT
        selection.nudge(16, 0)
        assert selection.box == Box(100, 100, 176, 99)

    def test_typed_box(self) -> None:
        selection = Selection(BOUNDS, aspect=AspectRatio(1, 1))
        assert selection.set_box(Box(0, 0, 50, 50)) is False
        assert selection.aspect == AspectRatio(1, 1)
        assert selection.set_box(Box(10, 20, 300, 100)) is True  # does not match 1:1, so it unlocks
        assert selection.aspect is None
        with pytest.raises(ValueError, match="does not fit"):
            selection.set_box(Box(700, 0, 200, 100))

    def test_locking_a_ratio_reshapes_around_the_centre(self) -> None:
        selection = Selection(BOUNDS, Box(300, 200, 200, 200))
        selection.set_aspect(AspectRatio(4, 1))
        assert selection.box == Box(200, 250, 400, 100)
        selection.set_aspect(None)
        assert selection.box == Box(200, 250, 400, 100)

    def test_locking_a_ratio_never_leaves_the_picture(self) -> None:
        selection = Selection(BOUNDS, Box(0, 0, 800, 600))
        selection.set_aspect(AspectRatio(16, 9))
        box = selection.box
        assert box is not None and box.fits_in(BOUNDS)
        assert box.width / box.height == pytest.approx(16 / 9, abs=0.01)

    def test_cycle_through_the_presets(self) -> None:
        selection = Selection(BOUNDS)
        seen = [selection.cycle_aspect() for _ in ASPECT_PRESETS]
        assert seen == [*ASPECT_PRESETS[1:], None]

    def test_custom_ratio_cycles_to_the_first_preset(self) -> None:
        selection = Selection(BOUNDS, aspect=AspectRatio(2.35, 1))
        assert selection.cycle_aspect() == ASPECT_PRESETS[1]

    def test_select_all(self) -> None:
        selection = Selection(BOUNDS)
        selection.select_all()
        assert selection.box == Box(0, 0, 800, 600)
        selection.set_aspect(AspectRatio(1, 1))
        selection.select_all()
        assert selection.box == Box(100, 0, 600, 600)

    def test_initial_box_must_fit(self) -> None:
        with pytest.raises(ValueError):
            Selection(BOUNDS, Box(0, 0, 900, 10))
