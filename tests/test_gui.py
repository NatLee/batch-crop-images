"""GUI smoke tests: start ``batch-crop``, drive the real window with mouse and keys, check the files.

They need a screen, so they only run when ``BATCH_CROP_GUI_TESTS=1``:

* Linux: inside ``tests/gui/Dockerfile`` (a virtual screen from Xvfb, input from xdotool).
* Windows: on the desktop session, with input posted to the window through the Win32 API.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import ClassVar, Protocol

import pytest
from conftest import MakeImage
from PIL import Image

REPO = Path(__file__).resolve().parents[1]
TITLE = "batch-crop"  # batch_crop.window.WINDOW_TITLE
WAIT = 30.0  # seconds to wait for the window to open or the program to finish


class Driver(Protocol):
    def find(self) -> None: ...
    def drag(self, start: tuple[int, int], end: tuple[int, int]) -> None: ...
    def keys(self, *names: str) -> None: ...
    def type(self, text: str) -> None: ...
    def close(self) -> None: ...


class Xdotool:
    """X11 (Xvfb) through xdotool. Key names are X keysym names such as Right or Return."""

    def __init__(self) -> None:
        self.window = ""

    def _run(self, *args: str) -> str:
        return subprocess.run(["xdotool", *args], capture_output=True, text=True, timeout=WAIT, check=True).stdout

    def find(self) -> None:
        found = self._run("search", "--sync", "--onlyvisible", "--name", f"^{TITLE}$")
        self.window = found.split()[0]
        time.sleep(0.5)  # let the first frame render

    def drag(self, start: tuple[int, int], end: tuple[int, int]) -> None:
        middle = ((start[0] + end[0]) // 2, (start[1] + end[1]) // 2)
        self._run("mousemove", "--window", self.window, *map(str, start), "mousedown", "1")
        for point in (middle, end):
            time.sleep(0.1)
            self._run("mousemove", "--window", self.window, *map(str, point))
        time.sleep(0.1)
        self._run("mouseup", "1")
        time.sleep(0.2)

    def keys(self, *names: str) -> None:
        try:
            self._run("key", "--window", self.window, "--delay", "80", *names)
        except subprocess.CalledProcessError:
            # Enter, Esc and q close the window on the key press, so xdotool may fail to send the
            # key release to a window that is already gone. Only a window that is still there is a failure.
            if self._exists():
                raise
        time.sleep(0.2)

    def _exists(self) -> bool:
        found = subprocess.run(["xdotool", "getwindowname", self.window], capture_output=True, timeout=WAIT)
        return found.returncode == 0

    def type(self, text: str) -> None:
        self._run("type", "--window", self.window, "--delay", "80", text)
        time.sleep(0.2)

    def close(self) -> None:
        self._run("windowclose", self.window)


class Win32:
    """Windows: posts the same messages a real mouse and keyboard would send to OpenCV's window."""

    WM_CLOSE, WM_KEYDOWN, WM_CHAR = 0x0010, 0x0100, 0x0102
    WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON = 0x0200, 0x0201, 0x0202, 0x0001
    VIRTUAL_KEYS: ClassVar[dict[str, int]] = {"Left": 0x25, "Up": 0x26, "Right": 0x27, "Down": 0x28}
    CHARACTERS: ClassVar[dict[str, int]] = {"Return": 13, "Escape": 27, "BackSpace": 8, "Tab": 9}

    def __init__(self) -> None:
        import ctypes

        self.user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        self.frame = self.picture = 0

    def find(self) -> None:
        deadline = time.monotonic() + WAIT
        while time.monotonic() < deadline:
            self.frame = self.user32.FindWindowW(None, TITLE)
            # OpenCV draws the picture in a child window; mouse messages go there.
            self.picture = self.user32.FindWindowExW(self.frame, None, None, None) if self.frame else 0
            if self.picture:
                time.sleep(0.5)
                return
            time.sleep(0.2)
        raise TimeoutError(f"no {TITLE} window after {WAIT} s")

    def _post(self, window: int, message: int, wparam: int, lparam: int = 0) -> None:
        self.user32.PostMessageW(window, message, wparam, lparam)
        time.sleep(0.05)

    def drag(self, start: tuple[int, int], end: tuple[int, int]) -> None:
        middle = ((start[0] + end[0]) // 2, (start[1] + end[1]) // 2)
        self._post(self.picture, self.WM_LBUTTONDOWN, self.MK_LBUTTON, start[0] | start[1] << 16)
        for x, y in (middle, end):
            self._post(self.picture, self.WM_MOUSEMOVE, self.MK_LBUTTON, x | y << 16)
        self._post(self.picture, self.WM_LBUTTONUP, 0, end[0] | end[1] << 16)
        time.sleep(0.2)

    def keys(self, *names: str) -> None:
        for name in names:
            if name in self.VIRTUAL_KEYS:
                self._post(self.frame, self.WM_KEYDOWN, self.VIRTUAL_KEYS[name])
            else:
                self._post(self.frame, self.WM_CHAR, self.CHARACTERS.get(name) or ord(name))
            time.sleep(0.05)
        time.sleep(0.2)

    def type(self, text: str) -> None:
        self.keys(*text)

    def close(self) -> None:
        self._post(self.frame, self.WM_CLOSE, 0)


def _driver() -> Driver | None:
    if os.environ.get("BATCH_CROP_GUI_TESTS") != "1":
        return None
    if sys.platform == "win32":
        return Win32()
    if os.environ.get("DISPLAY") and shutil.which("xdotool"):
        return Xdotool()
    return None


pytestmark = [
    pytest.mark.gui,
    pytest.mark.skipif(_driver() is None, reason="set BATCH_CROP_GUI_TESTS=1 on a desktop (see tests/gui/)"),
]


@pytest.fixture
def driver() -> Driver:
    found = _driver()
    assert found is not None
    return found


@pytest.fixture
def pictures(tmp_path: Path, make_image: MakeImage) -> Path:
    # 800x450 opens at 100% on any screen of 1024x768 or more, so window pixels are image pixels.
    make_image("in/a.png", 800, 450)
    make_image("in/b.png", 800, 450)
    return tmp_path


Launch = Callable[..., "subprocess.Popen[str]"]


@pytest.fixture
def launch(pictures: Path) -> Iterator[Launch]:
    """Start batch-crop on the test pictures; whatever happens, it is stopped after the test."""
    started: list[subprocess.Popen[str]] = []

    def start(*extra: str) -> subprocess.Popen[str]:
        env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(REPO / "src"), os.environ.get("PYTHONPATH", "")])}
        command = [sys.executable, "-m", "batch_crop", "-i", "in", "-o", "out", "--save-preset", "box.json", *extra]
        process = subprocess.Popen(
            command, cwd=pictures, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        started.append(process)
        return process

    yield start
    for process in started:
        if process.poll() is None:
            process.kill()
            process.communicate()


def finish(process: subprocess.Popen[str]) -> tuple[int, str]:
    try:
        output, _ = process.communicate(timeout=WAIT)
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate()
        pytest.fail(f"batch-crop did not finish:\n{output}")
    return process.returncode, output


def saved_box(folder: Path) -> str:
    box = json.loads((folder / "box.json").read_text(encoding="utf-8"))["box"]
    return f"{box['x']},{box['y']},{box['width']},{box['height']}"


def test_draw_nudge_and_crop(driver: Driver, pictures: Path, launch: Launch) -> None:
    process = launch()
    driver.find()
    driver.drag((500, 380), (100, 80))  # right to left: GitHub issue #2
    driver.keys("Right", "Right", "Right")
    driver.keys("Return")
    code, output = finish(process)
    assert code == 0, output
    assert saved_box(pictures) == "103,80,400,300"
    for name in ("a.png", "b.png"):
        with Image.open(pictures / "out" / name) as image:
            assert image.size == (400, 300)


def test_square_from_the_ratio_lock(driver: Driver, pictures: Path, launch: Launch) -> None:
    process = launch("--aspect", "1:1")
    driver.find()
    driver.drag((100, 80), (500, 380))  # wider than tall; the square stops at the bottom edge
    driver.keys("Return")
    code, output = finish(process)
    assert code == 0, output
    assert saved_box(pictures) == "100,80,370,370"


def test_typed_numbers(driver: Driver, pictures: Path, launch: Launch) -> None:
    process = launch("--aspect", "1:1")
    driver.find()
    driver.drag((100, 80), (500, 380))
    driver.keys("e", *["BackSpace"] * 20)
    driver.type("10,20,300,200")
    driver.keys("Return", "Return")  # apply the numbers (this unlocks 1:1), then crop
    code, output = finish(process)
    assert code == 0, output
    assert saved_box(pictures) == "10,20,300,200"


@pytest.mark.parametrize("how", ["q", "Escape", "close"])
def test_quitting_writes_nothing(driver: Driver, pictures: Path, launch: Launch, how: str) -> None:
    if how == "close" and isinstance(driver, Xdotool) and not os.environ.get("BATCH_CROP_GUI_WM"):
        pytest.skip("closing a window needs a window manager; Xvfb alone has none")
    process = launch()
    driver.find()
    driver.drag((100, 80), (500, 380))
    if how == "close":
        driver.close()
    else:
        driver.keys(how)
    code, output = finish(process)
    assert code == 1, output
    assert "Cancelled: nothing was written." in output
    assert not (pictures / "out").exists()
