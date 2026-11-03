"""The crop engine: decide what happens to every image, then do it (optionally in parallel).

Planning and doing are separate steps on purpose. Planning only reads image headers, asks any
questions up front and can be printed as a dry run; running the plan never needs to ask anything,
so it can use several threads.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from . import imageio
from .geometry import Anchor, Box, Size, round_half_up


class Mismatch(Enum):
    """What to do with an image whose size differs from the reference image the box was drawn on."""

    ASK = "ask"
    STRETCH = "stretch"
    SCALE = "scale"
    ANCHOR = "anchor"
    SKIP = "skip"


# One-line explanations, shown by --help and in the README.
MISMATCH_HELP: dict[Mismatch, str] = {
    Mismatch.ASK: "ask for each image; yes stretches it (the behaviour since 2019)",
    Mismatch.STRETCH: "resize the image to the reference size, then crop: every output has the box's size",
    Mismatch.SCALE: "scale the box with the image: the same part of every picture, sizes vary",
    Mismatch.ANCHOR: "keep the box's pixel size and its distance to --anchor (good for screenshots)",
    Mismatch.SKIP: "leave those images out",
}


class CropError(ValueError):
    """The box cannot be applied to an image (for example it falls outside it)."""


class Aborted(Exception):
    """The user declined to continue; nothing has been written yet."""


@dataclass(frozen=True)
class CropSpec:
    """A crop box together with the size of the image it was drawn on (what a preset file stores)."""

    box: Box
    reference: Size

    def __post_init__(self) -> None:
        if not self.box.fits_in(self.reference):
            raise CropError(f"box {self.box} does not fit in the {self.reference} reference image")


@dataclass(frozen=True)
class CropOp:
    """What to do with one image.

    ``source`` is the region to take, as (left, top, right, bottom) in the image's own pixels; it may be
    fractional when ``output_size`` is set, in which case that region is resampled to ``output_size``.
    """

    source: tuple[float, float, float, float]
    output_size: Size | None = None

    @property
    def result_size(self) -> Size:
        if self.output_size is not None:
            return self.output_size
        left, top, right, bottom = self.source
        return Size(int(right - left), int(bottom - top))


def operation_for(spec: CropSpec, size: Size, mode: Mismatch | None, anchor: Anchor = Anchor.CENTER) -> CropOp | None:
    """Work out how to crop an image of ``size``; ``None`` means skip it.

    Images with the reference size always get the box exactly as drawn, whatever ``mode`` says.
    For the others ``mode`` must already be decided: asking the user is the caller's job (see :func:`plan`).
    """
    box, ref = spec.box, spec.reference
    if size == ref:
        return _anchored(box, size, ref, (0.0, 0.0))
    if mode is Mismatch.ANCHOR:
        return _anchored(box, size, ref, anchor.fractions)
    sx, sy = size.width / ref.width, size.height / ref.height
    if mode is Mismatch.STRETCH:
        return CropOp((box.x * sx, box.y * sy, box.right * sx, box.bottom * sy), output_size=box.size)
    if mode is Mismatch.SCALE:
        try:
            scaled = Box.from_corners(box.x * sx, box.y * sy, box.right * sx, box.bottom * sy)
        except ValueError as exc:
            raise CropError(f"the scaled box is too small for this {size} image") from exc
        return CropOp((scaled.x, scaled.y, scaled.right, scaled.bottom))
    if mode is Mismatch.SKIP:
        return None
    raise ValueError(f"how to handle a {size} image has to be decided before planning (got {mode})")


def _anchored(box: Box, size: Size, ref: Size, fractions: tuple[float, float]) -> CropOp:
    """Keep the box's pixel size; move it by the given share of the size difference on each axis."""
    fx, fy = fractions
    x = box.x + round_half_up(fx * (size.width - ref.width))
    y = box.y + round_half_up(fy * (size.height - ref.height))
    if x < 0 or y < 0 or x + box.width > size.width or y + box.height > size.height:
        raise CropError(f"the {box.width}x{box.height} box does not fit in this {size} image")
    return CropOp((x, y, x + box.width, y + box.height))


@dataclass(frozen=True)
class Job:
    """One planned image: where it comes from, where it goes and what happens to it."""

    source: Path
    target: Path
    op: CropOp | None = None
    note: str = ""
    error: str = ""

    @property
    def will_write(self) -> bool:
        return self.op is not None and not self.error


# Called for every image whose size differs from the reference; returns the decided mode.
MismatchDecider = Callable[[Path, Size, Size], Mismatch]


def plan(
    sources: Sequence[Path],
    *,
    input_dir: Path,
    output_dir: Path,
    spec: CropSpec,
    decide: MismatchDecider,
    anchor: Anchor = Anchor.CENTER,
    skip_existing: bool = False,
    size_of: Callable[[Path], Size] = imageio.image_size,
) -> list[Job]:
    """Plan every image. Raises :class:`Aborted` if ``decide`` gives up; nothing is written here."""
    jobs: list[Job] = []
    for source in sources:
        target = output_dir / source.relative_to(input_dir)
        if skip_existing and target.exists():
            jobs.append(Job(source, target, note="already exists"))
            continue
        try:
            size = size_of(source)
        except OSError as exc:  # unreadable or not an image after all
            jobs.append(Job(source, target, error=f"cannot read image: {exc}"))
            continue
        mode = None if size == spec.reference else decide(source, size, spec.reference)
        try:
            op = operation_for(spec, size, mode, anchor)
        except CropError as exc:
            jobs.append(Job(source, target, error=str(exc)))
            continue
        jobs.append(Job(source, target, op, note=_describe(op, mode, size, spec)))
    return jobs


def _describe(op: CropOp | None, mode: Mismatch | None, size: Size, spec: CropSpec) -> str:
    if op is None:
        return f"{size}, not the reference {spec.reference}"
    if mode is None:
        return ""
    if mode is Mismatch.STRETCH:
        return f"stretched from {size} to {spec.reference}"
    left, top, right, bottom = op.source
    return f"{mode.value}: box {Box(int(left), int(top), int(right - left), int(bottom - top))} on {size}"


class Status(Enum):
    WRITTEN = "written"
    PLANNED = "would write"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class Result:
    job: Job
    status: Status
    message: str = ""


def run(
    jobs: Iterable[Job],
    *,
    quality: int = imageio.DEFAULT_QUALITY,
    workers: int = 0,
    dry_run: bool = False,
    on_result: Callable[[Result], None] = lambda result: None,
) -> list[Result]:
    """Carry out a plan; ``workers=0`` uses one thread per CPU core. Results come back in plan order."""

    def execute(job: Job) -> Result:
        if job.error:
            return Result(job, Status.FAILED, job.error)
        if job.op is None:
            return Result(job, Status.SKIPPED, job.note)
        if dry_run:
            return Result(job, Status.PLANNED, job.note)
        try:
            imageio.crop_file(job.source, job.target, job.op, quality=quality)
        except (OSError, ValueError) as exc:
            return Result(job, Status.FAILED, str(exc))
        return Result(job, Status.WRITTEN, job.note)

    jobs = list(jobs)
    count = workers if workers > 0 else (os.cpu_count() or 1)
    if count == 1 or len(jobs) <= 1:
        results = []
        for job in jobs:
            results.append(execute(job))
            on_result(results[-1])
        return results
    with ThreadPoolExecutor(max_workers=min(count, len(jobs))) as pool:
        results = []
        for result in pool.map(execute, jobs):
            results.append(result)
            on_result(result)
        return results
