"""The command line, end to end, without a display (the window is replaced where it would open)."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from conftest import MakeImage, origin_of
from PIL import Image

from batch_crop import __version__, window
from batch_crop.cli import main
from batch_crop.geometry import AspectRatio, Box

REPO = Path(__file__).resolve().parents[1]
BOX = "100,40,200,100"


def cli(*args: str | Path, stdin: str = "") -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main([str(arg) for arg in args], stdin=io.StringIO(stdin), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def sizes(folder: Path) -> dict[str, tuple[int, int]]:
    found = {}
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            with Image.open(path) as image:
                found[path.relative_to(folder).as_posix()] = image.size
    return found


@pytest.fixture
def folder(tmp_path: Path, make_image: MakeImage) -> Path:
    for name in ("1.png", "2.png", "10.png"):
        make_image(f"in/{name}", 400, 300)
    return tmp_path


def test_box_crops_every_image(folder: Path) -> None:
    code, out, err = cli("-i", folder / "in", "-o", folder / "out", "--box", BOX)
    assert (code, err) == (0, "")
    assert sizes(folder / "out") == {"1.png": (200, 100), "10.png": (200, 100), "2.png": (200, 100)}
    with Image.open(folder / "out" / "2.png") as image:
        assert origin_of(image) == (100, 40)
    assert "Done in" in out and "3 written, 0 skipped, 0 failed" in out


def test_main_py_keeps_the_original_command(folder: Path) -> None:
    command = [sys.executable, str(REPO / "main.py"), "--input_path", "in", "--output_path", "out", "--box", BOX]
    finished = subprocess.run(command, cwd=folder, capture_output=True, text=True, timeout=120, check=False)
    assert finished.returncode == 0, finished.stderr
    assert len(sizes(folder / "out")) == 3


def test_module_and_version() -> None:
    finished = subprocess.run(
        [sys.executable, "-m", "batch_crop", "--version"], capture_output=True, text=True, timeout=60, check=False
    )
    assert finished.stdout.strip() == f"batch-crop {__version__}"


def test_default_folders_are_the_demo_folders(tmp_path: Path, make_image: MakeImage, monkeypatch: Any) -> None:
    make_image("img/input/a.jpg", 64, 48)
    monkeypatch.chdir(tmp_path)
    assert cli("--box", "0,0,32,24")[0] == 0
    assert sizes(tmp_path / "img" / "result") == {"a.jpg": (32, 24)}


def test_missing_input_folder(tmp_path: Path) -> None:
    code, _, err = cli("-i", tmp_path / "nope", "-o", tmp_path / "out", "--box", BOX)
    assert code == 2
    assert "input folder not found" in err
    assert not (tmp_path / "out").exists()


def test_folder_without_images(tmp_path: Path) -> None:
    (tmp_path / "in").mkdir()
    code, _, err = cli("-i", tmp_path / "in", "--box", BOX)
    assert code == 2
    assert "no .jpg .jpeg .png files" in err and "--recursive" in err


def test_nested_output_folder_is_created(folder: Path) -> None:
    assert cli("-i", folder / "in", "-o", folder / "a" / "b" / "c", "--box", BOX)[0] == 0
    assert len(sizes(folder / "a" / "b" / "c")) == 3


def test_save_preset_then_reuse_it(folder: Path, make_image: MakeImage) -> None:
    preset = folder / "box.json"
    assert cli("-i", folder / "in", "-o", folder / "out", "--box", BOX, "--save-preset", preset)[0] == 0
    assert json.loads(preset.read_text(encoding="utf-8"))["box"] == {"x": 100, "y": 40, "width": 200, "height": 100}
    make_image("more/x.png", 400, 300)
    code, out, _ = cli("-i", folder / "more", "-o", folder / "out2", "--preset", preset)
    assert code == 0
    assert sizes(folder / "out2") == {"x.png": (200, 100)}
    assert "add --box" not in out  # already re-runnable, no hint needed


def test_dry_run_writes_nothing(folder: Path) -> None:
    code, out, _ = cli("-i", folder / "in", "-o", folder / "out", "--box", BOX, "--dry-run")
    assert code == 0
    assert not (folder / "out").exists()
    assert out.count("would write") == 3
    assert "Dry run: 3 would be written, 0 skipped, 0 failed. Nothing was written." in out


def test_skip_existing(folder: Path, make_image: MakeImage) -> None:
    keep = make_image("out/1.png", 7, 7)
    code, out, _ = cli("-i", folder / "in", "-o", folder / "out", "--box", BOX, "--skip-existing")
    assert code == 0
    assert sizes(folder / "out")["1.png"] == (7, 7) and keep.exists()
    assert "2 written, 1 skipped" in out
    assert cli("-i", folder / "in", "-o", folder / "out", "--box", BOX)[0] == 0  # default: overwrite
    assert sizes(folder / "out")["1.png"] == (200, 100)


class TestSizeMismatch:
    @pytest.fixture
    def mixed(self, tmp_path: Path, make_image: MakeImage) -> Path:
        make_image("in/a.png", 400, 300)
        make_image("in/b.png", 800, 450)
        return tmp_path

    def _crop(self, mixed: Path, *extra: str, stdin: str = "") -> tuple[int, str, str]:
        return cli("-i", mixed / "in", "-o", mixed / "out", "--box", BOX, *extra, stdin=stdin)

    def test_ask_yes_stretches(self, mixed: Path) -> None:
        code, out, err = self._crop(mixed, stdin="y\n")
        assert code == 0
        assert "b.png is 800x450, but the reference image is 400x300" in err
        assert "[Y/n]" in out
        assert sizes(mixed / "out") == {"a.png": (200, 100), "b.png": (200, 100)}

    def test_ask_default_is_yes(self, mixed: Path) -> None:
        assert self._crop(mixed, stdin="\n")[0] == 0

    def test_ask_no_stops_before_writing_anything(self, mixed: Path) -> None:
        code, out, _ = self._crop(mixed, stdin="n\n")
        assert code == 1
        assert "Stopped: you chose not to stretch b.png. Nothing was written." in out
        assert not (mixed / "out").exists()

    def test_ask_without_anybody_to_answer(self, mixed: Path) -> None:
        code, out, _ = self._crop(mixed, stdin="")
        assert code == 1
        assert "nobody answered" in out and "--mismatch" in out

    def test_ask_repeats_on_other_answers(self, mixed: Path) -> None:
        code, _, err = self._crop(mixed, stdin="maybe\nyes\n")
        assert code == 0
        assert "Please answer y or n." in err

    @pytest.mark.parametrize(
        ("extra", "expected"),
        [
            (("--mismatch", "stretch"), {"a.png": (200, 100), "b.png": (200, 100)}),
            (("--mismatch", "scale"), {"a.png": (200, 100), "b.png": (400, 150)}),
            (("--mismatch", "anchor"), {"a.png": (200, 100), "b.png": (200, 100)}),
            (("--mismatch", "skip"), {"a.png": (200, 100)}),
        ],
    )
    def test_modes(self, mixed: Path, extra: tuple[str, ...], expected: dict[str, tuple[int, int]]) -> None:
        assert self._crop(mixed, *extra)[0] == 0
        assert sizes(mixed / "out") == expected

    def test_anchor_side(self, mixed: Path) -> None:
        assert self._crop(mixed, "--mismatch", "anchor", "--anchor", "bottom-right")[0] == 0
        with Image.open(mixed / "out" / "b.png") as image:
            assert origin_of(image) == ((100 + 400) % 256, (40 + 150) % 256)

    def test_reference_image_choice(self, mixed: Path) -> None:
        code, _, _ = cli(
            "-i",
            mixed / "in",
            "-o",
            mixed / "out",
            "--box",
            "600,0,200,100",
            "--reference",
            "b.png",
            "--mismatch",
            "skip",
        )
        assert code == 0
        assert sizes(mixed / "out") == {"b.png": (200, 100)}


def test_missing_reference_image(folder: Path) -> None:
    code, _, err = cli("-i", folder / "in", "--box", BOX, "--reference", "nope.png")
    assert code == 2 and "reference image not found" in err


def test_box_larger_than_the_reference(folder: Path) -> None:
    code, _, err = cli("-i", folder / "in", "-o", folder / "out", "--box", "300,0,200,100")
    assert code == 2
    assert "does not fit in the 400x300 reference image" in err


def test_one_broken_file_does_not_stop_the_rest(folder: Path) -> None:
    (folder / "in" / "3.png").write_bytes(b"not a picture")
    code, out, err = cli("-i", folder / "in", "-o", folder / "out", "--box", BOX)
    assert code == 1
    assert "failed" in err and "3.png" in err
    assert "3 written, 0 skipped, 1 failed" in out


def test_parallel_and_serial_outputs_match(folder: Path) -> None:
    assert cli("-i", folder / "in", "-o", folder / "serial", "--box", BOX, "--jobs", "1")[0] == 0
    assert cli("-i", folder / "in", "-o", folder / "parallel", "--box", BOX, "--jobs", "4")[0] == 0
    for name in ("1.png", "2.png", "10.png"):
        with Image.open(folder / "serial" / name) as a, Image.open(folder / "parallel" / name) as b:
            assert np.array_equal(np.asarray(a), np.asarray(b))


def test_recursive_mirrors_folders_and_never_recrops_its_output(tmp_path: Path, make_image: MakeImage) -> None:
    make_image("in/a.png", 400, 300)
    make_image("in/trip/day1/b.png", 400, 300)
    for _ in range(2):
        assert cli("-i", tmp_path / "in", "-o", tmp_path / "in" / "cropped", "--box", BOX, "--recursive")[0] == 0
    assert sizes(tmp_path / "in" / "cropped") == {"a.png": (200, 100), "trip/day1/b.png": (200, 100)}


def test_extra_extensions(tmp_path: Path, make_image: MakeImage) -> None:
    make_image("in/a.webp", 400, 300)
    make_image("in/b.png", 400, 300)
    assert cli("-i", tmp_path / "in", "-o", tmp_path / "out", "--box", BOX, "--ext", "webp", "PNG")[0] == 0
    assert sizes(tmp_path / "out") == {"a.webp": (200, 100), "b.png": (200, 100)}


def test_quiet_prints_only_the_summary(folder: Path) -> None:
    code, out, _ = cli("-i", folder / "in", "-o", folder / "out", "--box", BOX, "--quiet")
    assert code == 0
    assert out.strip().splitlines() == [out.strip().splitlines()[-1]]
    assert out.startswith("Done in")


@pytest.mark.parametrize(
    "args",
    [
        ("--box", BOX, "--aspect", "16:9"),
        ("--box", "1,2,3"),
        ("--box", BOX, "--preset", "p.json"),
        ("--ext", "nope"),
        ("--quality", "0"),
        ("--jobs", "-1"),
        ("--mismatch", "sometimes"),
        ("--anchor", "middle"),
        ("--aspect", "wide"),
    ],
)
def test_bad_arguments_exit_with_2(folder: Path, args: tuple[str, ...], capsys: Any) -> None:
    with pytest.raises(SystemExit) as stopped:
        main(["-i", str(folder / "in"), *args])
    assert stopped.value.code == 2
    assert "error:" in capsys.readouterr().err


def test_help_explains_modes_and_keys(capsys: Any) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])
    text = capsys.readouterr().out
    for word in ("--input_path", "--output_path", "--box", "--preset", "stretch", "anchor", "Enter crops everything"):
        assert word in text


class TestWindow:
    """The CLI side of the window: what goes in, what comes out (the window itself is in test_gui.py)."""

    def test_drawn_box_is_used_and_can_be_repeated(self, folder: Path, monkeypatch: Any) -> None:
        calls: list[tuple[tuple[int, ...], AspectRatio | None]] = []

        def fake(image: np.ndarray, *, box: Box | None = None, aspect: AspectRatio | None = None) -> Box:
            calls.append((image.shape, aspect))
            return Box(100, 40, 200, 100)

        monkeypatch.setattr(window, "select_box", fake)
        code, out, _ = cli("-i", folder / "in", "-o", folder / "out", "--aspect", "2:1")
        assert code == 0
        assert calls == [((300, 400, 3), AspectRatio(2, 1))]
        assert "Draw the crop box on 1.png (400x300)" in out
        assert "add --box 100,40,200,100" in out
        assert len(sizes(folder / "out")) == 3

    def test_closing_the_window_writes_nothing(self, folder: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr(window, "select_box", lambda image, **kwargs: None)
        code, out, _ = cli("-i", folder / "in", "-o", folder / "out")
        assert code == 1
        assert "Cancelled: nothing was written." in out
        assert not (folder / "out").exists()


def test_names_the_console_cannot_show_do_not_crash(tmp_path: Path, make_image: MakeImage) -> None:
    make_image("in/写真.png", 40, 30)
    env = {**os.environ, "PYTHONIOENCODING": "ascii", "PYTHONPATH": os.pathsep.join([str(REPO / "src"), *sys.path])}
    finished = subprocess.run(
        [sys.executable, "-m", "batch_crop", "-i", "in", "-o", "out", "--box", "0,0,10,10"],
        cwd=tmp_path,
        capture_output=True,
        timeout=120,
        env=env,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    assert (tmp_path / "out" / "写真.png").exists()
    assert b"\\u5199\\u771f" in finished.stdout
