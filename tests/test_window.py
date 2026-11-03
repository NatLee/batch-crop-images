"""The crop window's logic and drawing, without opening a window (no display needed)."""

from __future__ import annotations

import sys

import cv2
import numpy as np
import pytest
from conftest import coded_image
from PIL import Image

from batch_crop import window
from batch_crop.geometry import AspectRatio, Box, Size
from batch_crop.window import CropWindow, initial_view, lookup_key, to_bgr

SCREEN = Size(1920, 1080)
LEFT = {"x11": 65361, "win32": 0x250000, "cocoa": 63234}


def make_window(width: int = 800, height: int = 450, **kwargs: object) -> CropWindow:
    picture = to_bgr(coded_image(width, height))
    return CropWindow(picture, screen=SCREEN, **kwargs)  # type: ignore[arg-type]


def mouse(win: CropWindow, event: int, x: int, y: int, flags: int = 0) -> None:
    win._on_mouse(event, x, y, flags)


def type_keys(win: CropWindow, text: str) -> None:
    for char in text:
        win.handle_key(ord(char))


class TestKeys:
    @pytest.mark.parametrize("backend", sorted(LEFT))
    def test_arrows_on_every_backend(self, backend: str) -> None:
        assert lookup_key(LEFT[backend]) == ("arrow", (-1, 0))

    def test_modifier_bits_are_ignored_for_plain_keys(self) -> None:
        assert lookup_key(0x100000 | ord("q")) == ("cancel", (0, 0))  # GTK with NumLock on
        assert lookup_key(0x100000 | 65363) == ("arrow", (1, 0))

    def test_unbound_keys(self) -> None:
        assert lookup_key(ord("z")) is None


class TestOpening:
    def test_big_picture_opens_inside_the_screen(self) -> None:
        # Issue #1: the window used to open larger than the screen.
        view = initial_view(Size(6000, 4000), SCREEN)
        assert view.width <= SCREEN.width * window.SCREEN_FILL
        assert view.height + window.STATUS_BAR_HEIGHT <= SCREEN.height * window.SCREEN_FILL

    def test_normal_picture_opens_at_100_percent(self) -> None:
        assert initial_view(Size(1280, 720), SCREEN) == Size(1280, 720)

    def test_small_picture_gets_a_usable_window(self) -> None:
        assert initial_view(Size(64, 64), SCREEN) == window.MIN_VIEW

    def test_screen_size_falls_back_without_tk(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "tkinter", None)  # import tkinter now raises ImportError
        assert window.screen_size() == window.FALLBACK_SCREEN

    @pytest.mark.parametrize(("flags", "delta"), [(120 << 16, 120), ((120 << 16) | 2, 120), (-(120 << 16), -120)])
    def test_wheel_delta_from_flags(self, flags: int, delta: int, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delattr(cv2, "getMouseWheelDelta", raising=False)
        assert window.wheel_delta(flags) == delta

    def test_qt_zooms_on_the_wheel_by_itself(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cv2, "currentUIFramework", lambda: "QT6", raising=False)
        assert window.backend_zooms_on_wheel()
        monkeypatch.setattr(cv2, "currentUIFramework", lambda: "WIN32UI", raising=False)
        assert not window.backend_zooms_on_wheel()


class TestToBgr:
    def test_rgb_becomes_bgr(self) -> None:
        frame = to_bgr(Image.new("RGB", (4, 2), (255, 128, 0)))
        assert frame.shape == (2, 4, 3) and frame.dtype == np.uint8
        assert tuple(frame[0, 0]) == (0, 128, 255)

    def test_transparent_parts_show_a_checkerboard(self) -> None:
        frame = to_bgr(Image.new("RGBA", (32, 32), (0, 0, 0, 0)))
        assert tuple(frame[0, 0]) == (255, 255, 255)
        assert tuple(frame[0, window.CHECKER]) == (204, 204, 204)

    @pytest.mark.parametrize("mode", ["L", "P", "CMYK", "1"])
    def test_other_modes(self, mode: str) -> None:
        assert to_bgr(coded_image(10, 6).convert(mode)).shape == (6, 10, 3)

    def test_16_bit_pictures_are_scaled_to_8_bit(self) -> None:
        frame = to_bgr(Image.new("I;16", (4, 4), 40000))
        assert tuple(frame[0, 0]) == (255, 255, 255)


class TestGestures:
    def test_draw_then_enter(self) -> None:
        win = make_window()
        mouse(win, cv2.EVENT_LBUTTONDOWN, 500, 380)
        mouse(win, cv2.EVENT_MOUSEMOVE, 300, 200, cv2.EVENT_FLAG_LBUTTON)
        mouse(win, cv2.EVENT_LBUTTONUP, 100, 80)
        assert win.selection.box == Box(100, 80, 400, 300)
        assert not win.done  # releasing the mouse no longer starts the crop; Enter does
        win.handle_key(13)
        assert win.done and win.result == Box(100, 80, 400, 300)

    def test_enter_without_a_box_explains(self) -> None:
        win = make_window()
        win.handle_key(13)
        assert not win.done
        assert "Draw a box first" in win._message

    @pytest.mark.parametrize("key", [27, ord("q")])
    def test_esc_and_q_quit(self, key: int) -> None:
        # The original checked `waitKey(20) and 0xFF == ord('q')`, which is never true.
        win = make_window(box=Box(1, 1, 10, 10))
        win.handle_key(key)
        assert win.done and win.result is None

    def test_double_click_inside_confirms(self) -> None:
        win = make_window(box=Box(100, 100, 200, 100))
        mouse(win, cv2.EVENT_LBUTTONDBLCLK, 900, 900)
        assert not win.done
        mouse(win, cv2.EVENT_LBUTTONDBLCLK, 200, 150)
        assert win.result == Box(100, 100, 200, 100)

    def test_arrows_tab_and_step(self) -> None:
        win = make_window(box=Box(100, 100, 200, 100))
        win.handle_key(LEFT["win32"])
        assert win.selection.box == Box(99, 100, 200, 100)
        win.handle_key(ord("]"))
        win.handle_key(65364)  # down, 10 px
        assert win.selection.box == Box(99, 110, 200, 100)
        win.handle_key(9)  # Tab: arrows now move the top-left corner
        win.handle_key(ord("["))
        win.handle_key(65363)  # right, 1 px
        assert win.selection.box == Box(100, 110, 199, 100)

    def test_typing_numbers(self) -> None:
        win = make_window(box=Box(1, 1, 10, 10))
        win.handle_key(ord("e"))
        assert win.entry == "1,1,10,10"
        for _ in range(len(win.entry)):
            win.handle_key(8)  # Backspace
        type_keys(win, "10, 20, 300, 200")
        win.handle_key(ord("q"))  # not a digit: ignored while typing, does not quit
        win.handle_key(13)
        assert win.entry is None and not win.done
        assert win.selection.box == Box(10, 20, 300, 200)

    def test_typing_a_box_that_does_not_fit(self) -> None:
        win = make_window()
        win.handle_key(ord("e"))
        type_keys(win, "700,0,200,100")
        win.handle_key(13)
        assert win.entry == "700,0,200,100"  # stays open so it can be corrected
        assert "does not fit" in win._message
        win.handle_key(27)
        assert win.entry is None and not win.done

    def test_ratio_key_and_initial_ratio(self) -> None:
        win = make_window(box=Box(0, 0, 400, 300), aspect=AspectRatio(4, 3))
        assert win.selection.aspect == AspectRatio(4, 3)
        win.handle_key(ord("r"))  # the next preset after 4:3
        box = win.selection.box
        assert win.selection.aspect == AspectRatio(3, 2)
        assert box is not None and box.width / box.height == pytest.approx(3 / 2, abs=0.02)

    def test_zoom_keys_and_wheel(self) -> None:
        win = make_window()
        win.handle_key(ord("+"))
        assert win.viewport.zoom == pytest.approx(window.ZOOM_STEP)
        win.handle_key(ord("0"))
        assert win.viewport.zoom == 1.0
        win.wheel_zoom = True
        mouse(win, cv2.EVENT_MOUSEWHEEL, 400, 200, 120 << 16)
        assert win.viewport.zoom == pytest.approx(window.ZOOM_STEP)
        win.wheel_zoom = False
        mouse(win, cv2.EVENT_MOUSEWHEEL, 400, 200, 120 << 16)
        assert win.viewport.zoom == pytest.approx(window.ZOOM_STEP)

    def test_drag_while_zoomed_maps_to_image_pixels(self) -> None:
        win = make_window(800, 450)
        win.viewport.zoom_by(2.0, around=(0, 0))  # top-left quarter of the picture, 2 screen px per pixel
        mouse(win, cv2.EVENT_LBUTTONDOWN, 20, 40)
        mouse(win, cv2.EVENT_LBUTTONUP, 220, 240)
        assert win.selection.box == Box(10, 20, 100, 100)

    def test_right_drag_pans(self) -> None:
        win = make_window()
        win.viewport.zoom_by(4.0)
        before = win.viewport.origin
        mouse(win, cv2.EVENT_RBUTTONDOWN, 300, 300)
        mouse(win, cv2.EVENT_MOUSEMOVE, 340, 300, cv2.EVENT_FLAG_RBUTTON)
        mouse(win, cv2.EVENT_RBUTTONUP, 340, 300)
        assert win.viewport.origin[0] < before[0]
        assert win.selection.box is None


class TestRender:
    def test_frame_has_the_view_plus_status_bar(self) -> None:
        win = make_window(800, 450)
        frame = win.render()
        assert frame.shape == (450 + window.STATUS_BAR_HEIGHT, 800, 3)

    def test_outside_the_box_is_dimmed(self) -> None:
        win = make_window(800, 450, box=Box(200, 100, 400, 200))
        plain = win.image
        frame = win.render()
        assert np.array_equal(frame[200, 400], plain[200, 400])  # inside: untouched
        assert int(frame[20, 400].sum()) < int(plain[20, 400].sum())  # outside: darker

    def test_help_and_entry_do_not_break_drawing(self) -> None:
        win = make_window(64, 64, box=Box(0, 0, 32, 32))
        win.handle_key(ord("h"))
        win.handle_key(ord("e"))
        assert win.render().shape[1] == window.MIN_VIEW.width


def test_help_lists_the_wheel_only_where_this_window_zooms_with_it() -> None:
    assert any("wheel" in keys for keys, _ in window.help_lines(wheel_zoom=True))
    assert not any("wheel" in keys for keys, _ in window.help_lines(wheel_zoom=False))
