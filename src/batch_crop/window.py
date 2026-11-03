"""The interactive crop window.

Draw, move and resize the box with the mouse, fine-tune it with the keyboard, then press Enter.
This is the only module that opens OpenCV windows: what the gestures do lives in ``selection.py``
and the zoom maths in ``viewport.py``, so both can be tested without a screen.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Mapping

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image

from .geometry import AspectRatio, Box, Size
from .selection import Handle, NudgeTarget, Selection
from .viewport import Viewport

Frame = NDArray[np.uint8]

WINDOW_TITLE = "batch-crop"
# OpenCV window flags: resizable, without OpenCV's own toolbar (we draw our own status bar).
WINDOW_FLAGS = cv2.WINDOW_NORMAL | cv2.WINDOW_FREERATIO | cv2.WINDOW_GUI_NORMAL

# The window opens at most this share of the screen, leaving room for the title bar and taskbar
# (big photos used to open larger than the screen, GitHub issue #1).
SCREEN_FILL = 0.85
# Screen size to assume when it cannot be read (Python built without Tk, or no display).
FALLBACK_SCREEN = Size(1280, 800)
# Small images are shown enlarged so the window and its status bar stay usable.
MIN_VIEW = Size(640, 360)

STATUS_BAR_HEIGHT = 28
FRAME_MS = 15  # how long each loop waits for a key
MESSAGE_SECONDS = 3.0
ZOOM_STEP = 1.25
NUDGE_STEPS = (1, 10, 50)  # pixels per arrow-key press, switched with [ and ]
HIT_TOLERANCE = 9  # screen pixels around an edge that grab it
HANDLE_RADIUS = 4
DIM_OUTSIDE = 0.4  # brightness of the picture outside the box
CHECKER = 8  # size of the checkerboard squares shown behind transparent pixels

# Colours are BGR, as OpenCV expects.
BACKGROUND = (38, 38, 38)
ACCENT = (0, 196, 255)
HANDLE_FILL = (255, 255, 255)
HANDLE_EDGE = (40, 40, 40)
BAR_FILL = (28, 28, 28)
BAR_TEXT = (225, 225, 225)
BAR_DIM = (150, 150, 150)
FONT = cv2.FONT_HERSHEY_SIMPLEX
FONT_SCALE = 0.45

# Key codes differ between OpenCV's window backends, so each arrow lists every code we know:
# X11 keysym (Qt and GTK on Linux), Win32 virtual key shifted left 16 bits, macOS Cocoa function key.
ARROW_KEYS: Mapping[int, tuple[int, int]] = {
    code: direction
    for codes, direction in (
        ((65361, 0x250000, 63234), (-1, 0)),
        ((65363, 0x270000, 63235), (1, 0)),
        ((65362, 0x260000, 63232), (0, -1)),
        ((65364, 0x280000, 63233), (0, 1)),
    )
    for code in codes
}
KEY_BINDINGS: Mapping[str, tuple[int, ...]] = {
    "confirm": (13, 10, 32),  # Enter (CR or LF), Space
    "cancel": (27, ord("q"), ord("Q")),  # Esc, Q
    "target": (9,),  # Tab
    "edit": (ord("e"), ord("E")),
    "ratio": (ord("r"), ord("R")),
    "all": (ord("a"), ord("A")),
    "zoom_in": (ord("+"), ord("=")),
    "zoom_out": (ord("-"), ord("_")),
    "zoom_fit": (ord("0"),),
    "step_down": (ord("["),),
    "step_up": (ord("]"),),
    "help": (ord("h"), ord("H"), ord("?")),
}
BACKSPACE_KEYS = (8, 127, 65288)
ENTRY_CHARACTERS = "0123456789, "
_ACTIONS = {code: action for action, codes in KEY_BINDINGS.items() for code in codes}

# OpenCV's Qt window (the one opencv-python uses on Linux) zooms the picture on the mouse wheel by
# itself, so the wheel is left to it there; zooming as well would double it.
BACKENDS_WITH_OWN_WHEEL_ZOOM = ("QT",)


def help_lines(wheel_zoom: bool) -> tuple[tuple[str, str], ...]:
    """The key list the H key shows; the wheel is only listed where this window handles it."""
    return (
        ("Drag outside the box", "draw a new box"),
        ("Drag inside / on an edge", "move it / resize it"),
        ("Arrow keys", "nudge the box (Tab: box, top-left, bottom-right)"),
        ("[ and ]", "nudge step: " + " / ".join(f"{step} px" for step in NUDGE_STEPS)),
        ("E", "type the box as X,Y,W,H"),
        ("R", "lock the ratio: free, 1:1, 4:3, 3:2, 16:9, ..."),
        ("A", "select the whole picture"),
        ("+ - 0" + (", mouse wheel" if wheel_zoom else ""), "zoom in / out / fit"),
        ("Right or middle drag", "move the picture when zoomed in"),
        ("Enter, Space, double-click", "crop every image with this box"),
        ("Esc or Q", "quit without cropping"),
    )


def screen_size() -> Size:
    """The size of the main screen, or :data:`FALLBACK_SCREEN` when it cannot be read."""
    try:
        import tkinter

        root = tkinter.Tk()
        try:
            root.withdraw()
            return Size(root.winfo_screenwidth(), root.winfo_screenheight())
        finally:
            root.destroy()
    except Exception:  # no Tk in this Python (issue #2 thread), or no display to ask
        return FALLBACK_SCREEN


def initial_view(image: Size, screen: Size) -> Size:
    """How big the picture area of the window is when it opens."""
    room_w = int(screen.width * SCREEN_FILL)
    room_h = int(screen.height * SCREEN_FILL) - STATUS_BAR_HEIGHT
    scale = min(1.0, room_w / image.width, room_h / image.height)
    width = min(max(MIN_VIEW.width, round(image.width * scale)), room_w)
    height = min(max(MIN_VIEW.height, round(image.height * scale)), room_h)
    return Size(max(width, 1), max(height, 1))


def to_bgr(image: Image.Image) -> Frame:
    """A picture OpenCV can show: 8-bit BGR, transparent parts on a checkerboard."""
    if image.mode in ("I", "F") or image.mode.startswith("I;16"):
        values = np.asarray(image, dtype=np.float64)
        peak = float(np.max(values)) if values.size else 0.0
        gray = np.rint(values * (255.0 / peak) if peak > 0 else values).astype(np.uint8)
        return np.ascontiguousarray(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR), dtype=np.uint8)
    if image.mode in ("RGBA", "LA", "PA", "La") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        rows, cols = np.indices((rgba.height, rgba.width))
        light = ((rows // CHECKER + cols // CHECKER) % 2 == 0)[..., None]
        checker = np.where(light, np.uint8(255), np.uint8(204)).repeat(3, axis=2)
        background = Image.fromarray(np.dstack([checker, np.full(light.shape, 255, np.uint8)]), "RGBA")
        image = Image.alpha_composite(background, rgba)
    rgb = np.asarray(image.convert("RGB"))
    return np.ascontiguousarray(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), dtype=np.uint8)


def wheel_delta(flags: int) -> int:
    """How far the mouse wheel turned: positive away from the user, negative towards.

    OpenCV documents the delta as a signed 16-bit number in the upper half of ``flags``;
    ``cv2.getMouseWheelDelta`` reads it, but some OpenCV builds (5.0) do not export that function.
    """
    get_delta = getattr(cv2, "getMouseWheelDelta", None)
    if callable(get_delta):
        return int(get_delta(flags))
    delta = (flags >> 16) & 0xFFFF
    return delta - 0x10000 if delta >= 0x8000 else delta


def backend_zooms_on_wheel() -> bool:
    framework = getattr(cv2, "currentUIFramework", None)  # OpenCV 4.6+
    name = str(framework()) if callable(framework) else ""
    return name.upper().startswith(BACKENDS_WITH_OWN_WHEEL_ZOOM)


def lookup_key(code: int) -> tuple[str, tuple[int, int]] | None:
    """Turn a ``cv2.waitKeyEx`` code into ("arrow", direction) or (action, (0, 0)); None if unbound.

    Some backends add modifier flags above the low 16 bits; those are ignored for plain keys.
    """
    for candidate in (code, code & 0xFFFF):
        if candidate in ARROW_KEYS:
            return "arrow", ARROW_KEYS[candidate]
        if candidate in _ACTIONS:
            return _ACTIONS[candidate], (0, 0)
    return None


class CropWindow:
    """The window state; :meth:`run` shows it and returns the confirmed box or ``None``."""

    def __init__(
        self,
        image: Frame,
        *,
        box: Box | None = None,
        aspect: AspectRatio | None = None,
        screen: Size | None = None,
        title: str = WINDOW_TITLE,
    ) -> None:
        height, width = image.shape[:2]
        self.image = image
        self.title = title
        self.selection = Selection(Size(width, height), box, aspect)
        self.viewport = Viewport(Size(width, height), initial_view(Size(width, height), screen or screen_size()))
        self.step_index = 0
        self.show_help = False
        self.entry: str | None = None
        self.result: Box | None = None
        self.done = False
        self.wheel_zoom = not backend_zooms_on_wheel()
        self._message = ""
        self._message_until = 0.0
        self._pointer = (0.0, 0.0)
        self._pan_from: tuple[int, int] | None = None
        self._base: Frame = image
        self._base_scale = 1.0
        self._dirty = True

    # ----- running -----------------------------------------------------------------------------

    def run(self) -> Box | None:
        view = self.viewport.view
        cv2.namedWindow(self.title, WINDOW_FLAGS)
        cv2.resizeWindow(self.title, view.width, view.height + STATUS_BAR_HEIGHT)
        cv2.setMouseCallback(self.title, self._on_mouse)
        seen_visible = False
        try:
            while not self.done:
                self._follow_window_size()
                if self._message and time.monotonic() > self._message_until:
                    self._message, self._dirty = "", True
                if self._dirty:
                    cv2.imshow(self.title, self.render())
                    self._dirty = False
                key = cv2.waitKeyEx(FRAME_MS)
                visible = cv2.getWindowProperty(self.title, cv2.WND_PROP_VISIBLE) >= 1
                if seen_visible and not visible:  # closed with the window's X button
                    break
                seen_visible = seen_visible or visible
                if key != -1:
                    self.handle_key(key)
        finally:
            with contextlib.suppress(cv2.error):  # already gone
                cv2.destroyWindow(self.title)
            cv2.waitKey(1)
        return self.result

    def _follow_window_size(self) -> None:
        """Render at the window's current size, so resizing the window never stretches the picture."""
        try:
            _, _, width, height = cv2.getWindowImageRect(self.title)
        except cv2.error:
            return
        view = Size(width, height - STATUS_BAR_HEIGHT)
        if view.width > 0 and view.height > 0 and view != self.viewport.view:
            self.viewport.resize(view)
            self._dirty = True

    def say(self, message: str) -> None:
        self._message = message
        self._message_until = time.monotonic() + MESSAGE_SECONDS
        self._dirty = True

    # ----- input -------------------------------------------------------------------------------

    def _on_mouse(self, event: int, x: int, y: int, flags: int, _param: object = None) -> None:
        self._pointer = (float(x), float(y))  # at 100% zoom these are the image pixels, as before
        ix, iy = self.viewport.to_image(*self._pointer)
        tolerance = HIT_TOLERANCE / self.viewport.scale
        selection = self.selection
        if event == cv2.EVENT_LBUTTONDOWN and y < self.viewport.view.height:
            selection.begin(ix, iy, tolerance)
        elif event == cv2.EVENT_LBUTTONUP and selection.dragging:
            selection.end(ix, iy)
        elif event == cv2.EVENT_LBUTTONDBLCLK:
            box = selection.box
            if box is not None and selection.hit_test(ix, iy, tolerance) is Handle.MOVE:
                self.confirm()
        elif event in (cv2.EVENT_RBUTTONDOWN, cv2.EVENT_MBUTTONDOWN):
            self._pan_from = (x, y)
        elif event in (cv2.EVENT_RBUTTONUP, cv2.EVENT_MBUTTONUP):
            self._pan_from = None
        elif event == cv2.EVENT_MOUSEWHEEL and self.wheel_zoom:
            step = ZOOM_STEP if wheel_delta(flags) > 0 else 1 / ZOOM_STEP
            self.viewport.zoom_by(step, around=self._pointer)
        elif event == cv2.EVENT_MOUSEMOVE:
            if selection.dragging:
                selection.update(ix, iy)
            elif self._pan_from is not None:
                self.viewport.pan(x - self._pan_from[0], y - self._pan_from[1])
                self._pan_from = (x, y)
            else:
                return  # plain hover: nothing to redraw
        else:
            return
        self._dirty = True

    def handle_key(self, code: int) -> None:
        self._dirty = True
        if self.entry is not None:
            self._entry_key(code)
            return
        found = lookup_key(code)
        if found is None:
            return
        action, (dx, dy) = found
        selection = self.selection
        if action == "arrow":
            step = NUDGE_STEPS[self.step_index]
            selection.nudge(dx * step, dy * step)
        elif action == "confirm":
            self.confirm()
        elif action == "cancel":
            self.result, self.done = None, True
        elif action == "target":
            self.say(f"Arrow keys now move the {selection.cycle_target().value}")
        elif action == "edit":
            self.entry = str(selection.box) if selection.box else ""
        elif action == "ratio":
            ratio = selection.cycle_aspect()
            self.say(f"Ratio locked to {ratio}" if ratio else "Ratio unlocked")
        elif action == "all":
            selection.select_all()
        elif action in ("zoom_in", "zoom_out"):
            self.viewport.zoom_by(ZOOM_STEP if action == "zoom_in" else 1 / ZOOM_STEP, around=self._pointer)
        elif action == "zoom_fit":
            self.viewport.reset()
        elif action in ("step_down", "step_up"):
            change = 1 if action == "step_up" else -1
            self.step_index = min(max(self.step_index + change, 0), len(NUDGE_STEPS) - 1)
            self.say(f"Arrow keys move {NUDGE_STEPS[self.step_index]} px")
        elif action == "help":
            self.show_help = not self.show_help

    def _entry_key(self, code: int) -> None:
        """Keys while typing a box: digits and separators, Backspace, Enter to apply, Esc to cancel."""
        entry = self.entry or ""
        char = code & 0xFFFF
        if char in (13, 10):
            try:
                unlocked = self.selection.set_box(Box.parse(entry))
            except ValueError as exc:
                self.say(str(exc))
                return
            self.entry = None
            self.say("Ratio unlocked to fit the typed box" if unlocked else f"Box set to {self.selection.box}")
        elif char == 27:
            self.entry = None
        elif char in BACKSPACE_KEYS or code in BACKSPACE_KEYS:
            self.entry = entry[:-1]
        elif 0 < char < 128 and chr(char) in ENTRY_CHARACTERS:
            self.entry = entry + chr(char)

    def confirm(self) -> None:
        if self.selection.box is None:
            self.say("Draw a box first: drag on the picture")
            return
        self.result, self.done = self.selection.box, True

    # ----- drawing -----------------------------------------------------------------------------

    def render(self) -> Frame:
        frame = self._render_picture()
        box = self.selection.box
        if box is not None:
            self._draw_box(frame, box)
        if self.show_help:
            _draw_help(frame, help_lines(self.wheel_zoom))
        return np.vstack((frame, self._render_status(frame.shape[1])))

    def _render_picture(self) -> Frame:
        viewport = self.viewport
        base_scale = min(1.0, viewport.scale)
        if base_scale != self._base_scale:
            # Shrink once with area averaging (sharp, no moire); the per-frame warp then never shrinks much.
            if base_scale < 1.0:
                height, width = self.image.shape[:2]
                size = (max(1, round(width * base_scale)), max(1, round(height * base_scale)))
                self._base = np.asarray(cv2.resize(self.image, size, interpolation=cv2.INTER_AREA), dtype=np.uint8)
            else:
                self._base = self.image
            self._base_scale = base_scale
        matrix = np.array(viewport.affine(self._base_scale), dtype=np.float64)
        interpolation = cv2.INTER_NEAREST if viewport.scale >= 2 else cv2.INTER_LINEAR
        view = viewport.view
        warped = cv2.warpAffine(
            self._base,
            matrix,
            (view.width, view.height),
            flags=interpolation,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=BACKGROUND,
        )
        return np.asarray(warped, dtype=np.uint8)

    def _view_rect(self, box: Box) -> tuple[int, int, int, int]:
        x0, y0 = self.viewport.to_view(box.x, box.y)
        x1, y1 = self.viewport.to_view(box.right, box.bottom)
        return round(x0), round(y0), round(x1), round(y1)

    def _draw_box(self, frame: Frame, box: Box) -> None:
        x0, y0, x1, y1 = self._view_rect(box)
        height, width = frame.shape[:2]
        cx0, cy0, cx1, cy1 = max(x0, 0), max(y0, 0), min(x1, width), min(y1, height)
        inside = frame[cy0:cy1, cx0:cx1].copy() if cx1 > cx0 and cy1 > cy0 else None
        frame[:] = (frame * DIM_OUTSIDE).astype(np.uint8)
        if inside is not None:
            frame[cy0:cy1, cx0:cx1] = inside
        cv2.rectangle(frame, (x0, y0), (x1 - 1, y1 - 1), ACCENT, 1, cv2.LINE_AA)
        active = self.selection.target.handle
        for handle in Handle:
            hx, hy = handle.sides
            if not (hx or hy):
                continue
            px = x0 if hx < 0 else x1 - 1 if hx > 0 else (x0 + x1) // 2
            py = y0 if hy < 0 else y1 - 1 if hy > 0 else (y0 + y1) // 2
            fill = ACCENT if handle is active else HANDLE_FILL
            corner_a = (px - HANDLE_RADIUS, py - HANDLE_RADIUS)
            corner_b = (px + HANDLE_RADIUS, py + HANDLE_RADIUS)
            cv2.rectangle(frame, corner_a, corner_b, fill, -1)
            cv2.rectangle(frame, corner_a, corner_b, HANDLE_EDGE, 1)

    def _render_status(self, width: int) -> Frame:
        bar = np.full((STATUS_BAR_HEIGHT, width, 3), BAR_FILL, dtype=np.uint8)
        baseline = STATUS_BAR_HEIGHT - 9
        if self.entry is not None:
            _text(bar, f"Type X,Y,W,H and press Enter (Esc cancels):  {self.entry}_", 10, baseline, ACCENT)
            return bar
        hint = "Enter: crop all   Esc: quit   H: help"
        hint_width = cv2.getTextSize(hint, FONT, FONT_SCALE, 1)[0][0]
        right_edge = width - 10
        if hint_width + 10 < width // 2:
            _text(bar, hint, right_edge - hint_width, baseline, BAR_DIM)
            right_edge -= hint_width + 20
        box = self.selection.box
        parts = [f"x {box.x}  y {box.y}  w {box.width}  h {box.height}" if box else "Drag on the picture to draw a box"]
        if self._message:
            parts.append(self._message)
        else:
            ratio = self.selection.aspect
            target = self.selection.target
            nudge = "" if target is NudgeTarget.BOX else f" {target.value}"
            step = NUDGE_STEPS[self.step_index]
            parts.append(f"ratio {ratio or 'free'}   zoom {self.viewport.scale:.0%}   arrows{nudge} {step} px")
        x = 10
        for index, part in enumerate(parts):
            colour = ACCENT if index == 1 and self._message else BAR_TEXT
            part_width = cv2.getTextSize(part, FONT, FONT_SCALE, 1)[0][0]
            if x + part_width > right_edge:
                break
            _text(bar, part, x, baseline, colour)
            x += part_width + 24
        return bar


def _text(frame: Frame, text: str, x: int, y: int, colour: tuple[int, int, int]) -> None:
    cv2.putText(frame, text, (x, y), FONT, FONT_SCALE, colour, 1, cv2.LINE_AA)


def _draw_help(frame: Frame, lines: tuple[tuple[str, str], ...]) -> None:
    line_height = 22
    key_width = max(cv2.getTextSize(keys, FONT, FONT_SCALE, 1)[0][0] for keys, _ in lines)
    panel_height = line_height * len(lines) + 20
    panel_width = min(frame.shape[1] - 20, key_width + 380)
    x0, y0 = 10, 10
    region = frame[y0 : y0 + panel_height, x0 : x0 + panel_width]
    region[:] = (region * 0.25).astype(np.uint8)
    for row, (keys, meaning) in enumerate(lines):
        y = y0 + 22 + row * line_height
        _text(frame, keys, x0 + 12, y, ACCENT)
        _text(frame, meaning, x0 + 28 + key_width, y, BAR_TEXT)


def select_box(
    image: Frame,
    *,
    box: Box | None = None,
    aspect: AspectRatio | None = None,
    screen: Size | None = None,
) -> Box | None:
    """Show ``image`` (BGR) and let the user pick a box; ``None`` if they quit."""
    return CropWindow(image, box=box, aspect=aspect, screen=screen).run()
