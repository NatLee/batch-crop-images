"""The ``batch-crop`` command (also ``python -m batch_crop`` and the original ``python main.py``)."""

from __future__ import annotations

import argparse
import contextlib
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

from . import __version__, imageio, preset
from .engine import MISMATCH_HELP, Aborted, CropError, CropSpec, Mismatch, MismatchDecider, Result, Status, plan, run
from .geometry import Anchor, AspectRatio, Box, Size

# The original script's defaults: its demo pictures live in these folders.
DEFAULT_INPUT = Path("img/input")
DEFAULT_OUTPUT = Path("img/result")

EXIT_OK = 0
EXIT_FAILED = 1  # some images failed, or the user cancelled
EXIT_USAGE = 2  # bad arguments or input (argparse uses 2 as well)
EXIT_INTERRUPTED = 130  # Ctrl+C, as shells report it

PROGRESS_WIDTH = 24

EXAMPLES = """\
how images of another size are handled (--mismatch):
{modes}

examples:
  batch-crop -i photos -o cropped                    draw the box, crop the folder
  batch-crop -i photos -o cropped --save-preset box.json
  batch-crop -i more-photos -o cropped --preset box.json      same box, no window
  batch-crop -i shots -o out --box 0,0,1280,720 --mismatch anchor --anchor bottom-right
  batch-crop -i photos -o cropped --aspect 1:1 --recursive --dry-run

In the window: drag to draw, drag the box or its edges to adjust, arrow keys nudge,
E types exact numbers, R locks a ratio, + / - zoom, Enter crops everything, Esc quits.
"""


class UsageError(Exception):
    """A problem with the arguments or the input folder; reported without a traceback."""


class Console:
    """Messages to stdout, problems to stderr, questions on stdin."""

    def __init__(
        self,
        *,
        quiet: bool = False,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
        stdin: TextIO | None = None,
    ) -> None:
        self.quiet = quiet
        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr
        self.stdin = stdin or sys.stdin

    def info(self, message: str) -> None:
        if not self.quiet:
            print(message, file=self.stdout, flush=True)

    def result(self, message: str) -> None:
        """Printed even with --quiet (the summary)."""
        print(message, file=self.stdout, flush=True)

    def problem(self, message: str) -> None:
        print(message, file=self.stderr, flush=True)

    def ask(self, question: str) -> str | None:
        """Ask on the terminal; ``None`` when there is nobody to answer (input closed)."""
        print(question, end="", file=self.stdout, flush=True)
        line = self.stdin.readline()
        return line if line else None


class AskUser:
    """``--mismatch ask``: the original question, asked while planning, before anything is written."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self.hinted = False

    def __call__(self, path: Path, size: Size, reference: Size) -> Mismatch:
        console = self.console
        console.problem(f"Notice: {path.name} is {size}, but the reference image is {reference}.")
        if not self.hinted:
            console.problem("        (Answer once for all images with --mismatch stretch, scale, anchor or skip.)")
            self.hinted = True
        while True:
            answer = console.ask(f"Stretch it to {reference} before cropping? The original file is not changed. [Y/n] ")
            if answer is None:
                raise Aborted("nobody answered the size question (input closed); choose with --mismatch")
            answer = answer.strip().lower()
            if answer in ("", "y", "yes"):
                return Mismatch.STRETCH
            if answer in ("n", "no"):
                raise Aborted(f"you chose not to stretch {path.name}")
            console.problem("Please answer y or n.")


class Progress:
    """A progress bar on a terminal; one line per image when the output is a log or a dry run."""

    def __init__(self, total: int, console: Console, *, dry_run: bool) -> None:
        self.total = total
        self.done = 0
        self.console = console
        self.bar = not console.quiet and not dry_run and _is_terminal(console.stderr)
        self.lines = not console.quiet and not self.bar

    def __call__(self, result: Result) -> None:
        self.done += 1
        job = result.job
        if result.status is Status.FAILED:
            self._clear()
            self.console.problem(f"failed      {job.source}: {result.message}")
        elif self.lines:
            note = f"  ({result.message})" if result.message else ""
            self.console.info(f"{result.status.value:<11} {job.target}{note}")
        if self.bar:
            filled = PROGRESS_WIDTH * self.done // max(self.total, 1)
            bar = "#" * filled + "." * (PROGRESS_WIDTH - filled)
            self.console.stderr.write(f"\r[{bar}] {self.done}/{self.total}  {job.source.name[:40]:<40}")
            self.console.stderr.flush()

    def close(self) -> None:
        self._clear()

    def _clear(self) -> None:
        if self.bar and self.done:
            self.console.stderr.write("\r" + " " * (PROGRESS_WIDTH + 60) + "\r")
            self.console.stderr.flush()


def _is_terminal(stream: TextIO) -> bool:
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False


# ----- arguments -------------------------------------------------------------------------------


def _argument(parse: type[Box] | type[AspectRatio]) -> Callable[[str], object]:
    def convert(text: str) -> object:
        try:
            return parse.parse(text)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc

    convert.__name__ = parse.__name__
    return convert


def _int_between(low: int, high: int | None) -> Callable[[str], int]:
    def convert(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            value = low - 1
        if value < low or (high is not None and value > high):
            limit = f"from {low} to {high}" if high is not None else f"of at least {low}"
            raise argparse.ArgumentTypeError(f"expected a whole number {limit}, got {text!r}")
        return value

    return convert


def build_parser() -> argparse.ArgumentParser:
    modes = "\n".join(f"  {mode.value:<8} {MISMATCH_HELP[mode]}" for mode in Mismatch)
    parser = argparse.ArgumentParser(
        prog="batch-crop",
        description="Draw a crop box on one image; every image in the folder is cropped the same way.",
        epilog=EXAMPLES.format(modes=modes),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    files = parser.add_argument_group("images")
    files.add_argument(
        "-i",
        "--input",
        "--input_path",
        dest="input",
        type=Path,
        default=DEFAULT_INPUT,
        metavar="DIR",
        help="folder with the images to crop (default: %(default)s)",
    )
    files.add_argument(
        "-o",
        "--output",
        "--output_path",
        dest="output",
        type=Path,
        default=DEFAULT_OUTPUT,
        metavar="DIR",
        help="folder for the cropped copies, created when missing (default: %(default)s)",
    )
    files.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="include sub-folders; the output folder gets the same sub-folders",
    )
    files.add_argument(
        "--ext",
        nargs="+",
        default=list(imageio.DEFAULT_EXTENSIONS),
        metavar="EXT",
        help="file extensions to crop, in any letter case (default: jpg jpeg png)",
    )
    files.add_argument(
        "--reference",
        type=Path,
        metavar="IMAGE",
        help="image to draw the box on and to compare sizes with (default: the first by name)",
    )

    box = parser.add_argument_group("crop box")
    source = box.add_mutually_exclusive_group()
    source.add_argument(
        "--box",
        type=_argument(Box),
        metavar="X,Y,W,H",
        help="crop this box (pixels of the reference image) instead of drawing it",
    )
    source.add_argument("--preset", type=Path, metavar="FILE", help="use a box saved earlier with --save-preset")
    box.add_argument(
        "--save-preset",
        type=Path,
        metavar="FILE",
        help="save the box to FILE, to re-run the batch later without the window",
    )
    box.add_argument(
        "--aspect",
        type=_argument(AspectRatio),
        metavar="W:H",
        help="lock the box in the window to a ratio such as 16:9 or 1:1 (R in the window cycles)",
    )

    sizes = parser.add_argument_group("images of another size than the reference")
    sizes.add_argument(
        "--mismatch",
        choices=[mode.value for mode in Mismatch],
        default=Mismatch.ASK.value,
        metavar="MODE",
        help="ask, stretch, scale, anchor or skip, explained below (default: %(default)s)",
    )
    sizes.add_argument(
        "--anchor",
        choices=[anchor.value for anchor in Anchor],
        default=Anchor.CENTER.value,
        metavar="SIDE",
        help="for --mismatch anchor: center, top, bottom-right, ... (default: %(default)s)",
    )

    output = parser.add_argument_group("output")
    output.add_argument(
        "--quality",
        type=_int_between(1, 100),
        default=imageio.DEFAULT_QUALITY,
        metavar="Q",
        help="JPEG and WebP quality, 1-100 (default: %(default)s, the same as before)",
    )
    output.add_argument(
        "--skip-existing",
        action="store_true",
        help="keep files that already exist in the output folder (default: overwrite them)",
    )
    output.add_argument("-n", "--dry-run", action="store_true", help="show what would be written; write nothing")

    running = parser.add_argument_group("running")
    running.add_argument(
        "-j",
        "--jobs",
        type=_int_between(0, None),
        default=0,
        metavar="N",
        help="images processed at the same time (default: 0, one per CPU core)",
    )
    running.add_argument("--quiet", action="store_true", help="print only problems and the summary")
    running.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _check(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.aspect is not None and (args.box is not None or args.preset is not None):
        parser.error("--aspect locks the box in the window, so it cannot be used with --box or --preset")
    known = imageio.known_extensions()
    args.ext = [imageio.normalize_extension(ext) for ext in args.ext]
    unknown = [ext for ext in args.ext if ext not in known]
    if unknown:
        parser.error(f"--ext: no image format uses {', '.join(unknown)}")


# ----- running ---------------------------------------------------------------------------------


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the command and return its exit code (the streams default to the terminal)."""
    _tolerate_unprintable_names()
    parser = build_parser()
    args = parser.parse_args(argv)
    _check(args, parser)
    console = Console(quiet=args.quiet, stdout=stdout, stderr=stderr, stdin=stdin)
    try:
        return _run(args, console)
    except UsageError as exc:
        console.problem(f"error: {exc}")
        return EXIT_USAGE
    except KeyboardInterrupt:
        console.problem("\nInterrupted. Files already written are complete; the rest were not started.")
        return EXIT_INTERRUPTED


def _run(args: argparse.Namespace, console: Console) -> int:
    input_dir: Path = args.input
    output_dir: Path = args.output
    if not input_dir.is_dir():
        raise UsageError(f"input folder not found: {input_dir.absolute()} (pass the folder with -i DIR)")
    sources = imageio.discover_images(input_dir, args.ext, recursive=args.recursive, exclude=output_dir)
    console.info(f"Input:  {input_dir.absolute()}  ({len(sources)} image{'' if len(sources) == 1 else 's'})")
    console.info(f"Output: {output_dir.absolute()}")
    if not sources:
        where = "" if args.recursive else " (add --recursive to look in sub-folders)"
        raise UsageError(f"no {' '.join(args.ext)} files in {input_dir}{where}")

    reference = _reference(args.reference, input_dir, sources)
    spec = _crop_spec(args, reference, console)
    if spec is None:
        console.result("Cancelled: nothing was written.")
        return EXIT_FAILED
    console.info(f"Crop box: {spec.box} (x,y,width,height) on the {spec.reference} reference image {reference.name}")
    if args.save_preset is not None:
        preset.save_preset(args.save_preset, spec)
        console.info(
            f"Saved the box to {args.save_preset}; add --preset {args.save_preset} to skip the window next time."
        )
    elif args.box is None and args.preset is None:
        console.info(f"To crop the same way without the window, add --box {spec.box}")

    decide = AskUser(console) if args.mismatch == Mismatch.ASK.value else _always(Mismatch(args.mismatch))
    try:
        jobs = plan(
            sources,
            input_dir=input_dir,
            output_dir=output_dir,
            spec=spec,
            decide=decide,
            anchor=Anchor(args.anchor),
            skip_existing=args.skip_existing,
        )
    except Aborted as exc:
        console.result(f"Stopped: {exc}. Nothing was written.")
        return EXIT_FAILED

    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
    progress = Progress(len(jobs), console, dry_run=args.dry_run)
    started = time.monotonic()
    try:
        results = run(jobs, quality=args.quality, workers=args.jobs, dry_run=args.dry_run, on_result=progress)
    finally:
        progress.close()
    return _summarise(results, console, seconds=time.monotonic() - started, dry_run=args.dry_run)


def _reference(argument: Path | None, input_dir: Path, sources: list[Path]) -> Path:
    if argument is None:
        return sources[0]
    for candidate in (argument, input_dir / argument):
        if candidate.is_file():
            return candidate
    raise UsageError(f"reference image not found: {argument}")


def _crop_spec(args: argparse.Namespace, reference: Path, console: Console) -> CropSpec | None:
    """The box from --preset, --box or the window (None if the window was closed without a box)."""
    try:
        if args.preset is not None:
            return preset.load_preset(args.preset)
        if args.box is not None:
            return CropSpec(args.box, imageio.image_size(reference))
    except (preset.PresetError, CropError) as exc:
        raise UsageError(str(exc)) from exc
    except OSError as exc:
        raise UsageError(f"cannot read the reference image {reference}: {exc}") from exc

    try:
        from . import window  # OpenCV is only needed for the window
    except ImportError as exc:
        raise UsageError(f"the crop window needs OpenCV ({exc}); use --box or --preset to crop without it") from exc
    try:
        picture = imageio.open_upright(reference)
    except OSError as exc:
        raise UsageError(f"cannot read the reference image {reference}: {exc}") from exc
    size = Size(*picture.size)
    console.info(f"Draw the crop box on {reference.name} ({size}) in the window, then press Enter. H shows all keys.")
    box = window.select_box(window.to_bgr(picture), aspect=args.aspect)
    return CropSpec(box, size) if box is not None else None


def _always(mode: Mismatch) -> MismatchDecider:
    def decide(path: Path, size: Size, reference: Size) -> Mismatch:
        return mode

    return decide


def _summarise(results: list[Result], console: Console, *, seconds: float, dry_run: bool) -> int:
    counts = {status: sum(result.status is status for result in results) for status in Status}
    failed = counts[Status.FAILED]
    if dry_run:
        console.result(
            f"Dry run: {counts[Status.PLANNED]} would be written, {counts[Status.SKIPPED]} skipped, "
            f"{failed} failed. Nothing was written."
        )
    else:
        console.result(
            f"Done in {seconds:.1f} s: {counts[Status.WRITTEN]} written, "
            f"{counts[Status.SKIPPED]} skipped, {failed} failed."
        )
    return EXIT_FAILED if failed else EXIT_OK


def _tolerate_unprintable_names() -> None:
    """Print file names the console cannot encode as escapes instead of crashing.

    A Windows console redirected to a file uses a legacy code page that cannot show, say, Japanese
    names; without this a crop could stop half-way just because a progress line failed to print.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            with contextlib.suppress(ValueError, OSError):
                reconfigure(errors="backslashreplace")
