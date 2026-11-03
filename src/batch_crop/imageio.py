"""Finding, reading and writing image files.

Everything goes through Pillow, which reads paths with any characters in them (``cv2.imread`` cannot
open non-ASCII paths on Windows), keeps transparency and bit depth, and can carry EXIF data and the
colour profile (ICC) over to the cropped file. OpenCV is only used to draw the interactive window.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import ExifTags, Image, ImageOps

from .geometry import Size

if TYPE_CHECKING:
    from .engine import CropOp

# The extensions the tool has always cropped (matched without regard to case).
DEFAULT_EXTENSIONS: tuple[str, ...] = (".jpg", ".jpeg", ".png")

# JPEG/WebP quality when the user does not pass --quality. OpenCV's imwrite, which saved the files
# before version 1.0, uses 95 for JPEG, so results look the same as they always did.
DEFAULT_QUALITY = 95

# Resampling filter for --mismatch stretch (the only mode that resizes pixels).
STRETCH_RESAMPLE = Image.Resampling.LANCZOS

# Metadata copied from the source file to the cropped file when the output format supports it.
# Pillow keeps these in ``Image.info``; formats that cannot store one simply ignore it.
PRESERVED_INFO: tuple[str, ...] = ("exif", "icc_profile", "dpi", "transparency")

# EXIF orientations that turn the picture by 90 degrees, so width and height swap.
_SIDEWAYS_ORIENTATIONS = frozenset({5, 6, 7, 8})

# Written next to the target and renamed into place, so an interrupted run never leaves half a file.
_PARTIAL_SUFFIX = ".part"

_DIGITS = re.compile(r"(\d+)")


def normalize_extension(ext: str) -> str:
    """``"JPG"``, ``"jpg"`` and ``".jpg"`` all become ``".jpg"``."""
    ext = ext.strip().lower()
    return ext if ext.startswith(".") else f".{ext}"


def known_extensions() -> frozenset[str]:
    """Every file extension Pillow knows about, for checking ``--ext`` early."""
    return frozenset(Image.registered_extensions())


def natural_key(path: Path) -> tuple[tuple[int, str | int], ...]:
    """Sort key that puts ``img2.jpg`` before ``img10.jpg`` and ignores case."""
    key: list[tuple[int, str | int]] = []
    for part in path.parts:
        for piece in _DIGITS.split(part.lower()):
            if piece:
                key.append((0, int(piece)) if piece.isdigit() else (1, piece))
        key.append((1, "/"))
    return tuple(key)


def discover_images(
    folder: Path,
    extensions: Iterable[str] = DEFAULT_EXTENSIONS,
    *,
    recursive: bool = False,
    exclude: Path | None = None,
) -> list[Path]:
    """List image files in ``folder`` (and its sub-folders when ``recursive``), in natural order.

    ``exclude`` is a folder to leave out, so writing the output inside the input folder never makes a
    later run crop its own results.
    """
    wanted = {normalize_extension(ext) for ext in extensions}
    skip = exclude.resolve() if exclude is not None and exclude.resolve() != folder.resolve() else None

    def walk(current: Path) -> Iterator[Path]:
        for entry in current.iterdir():
            if entry.is_dir():
                if recursive and (skip is None or entry.resolve() != skip):
                    yield from walk(entry)
            elif entry.suffix.lower() in wanted:
                yield entry

    return sorted(walk(folder), key=lambda path: natural_key(path.relative_to(folder)))


def image_size(path: Path) -> Size:
    """The size of the picture as people see it (EXIF rotation applied), read from the header only."""
    with Image.open(path) as image:
        width, height = image.size
        orientation = image.getexif().get(ExifTags.Base.Orientation, 1)
    return Size(height, width) if orientation in _SIDEWAYS_ORIENTATIONS else Size(width, height)


def open_upright(path: Path) -> Image.Image:
    """Load an image and turn it the way its EXIF orientation says, like a photo viewer would."""
    with Image.open(path) as image:
        image.load()
        upright = ImageOps.exif_transpose(image)
    return upright


def apply(image: Image.Image, op: CropOp) -> Image.Image:
    """Cut (and for --mismatch stretch, resample) the region ``op`` describes."""
    if op.output_size is not None:
        return image.resize(op.output_size, STRETCH_RESAMPLE, box=op.source)
    left, top, right, bottom = (int(value) for value in op.source)
    return image.crop((left, top, right, bottom))


def save(image: Image.Image, target: Path, *, quality: int = DEFAULT_QUALITY) -> None:
    """Save in the format the target's extension names, keeping metadata the format can hold.

    If the format cannot store the picture's colour mode (say a transparent picture saved as
    ``.jpg``), the picture is flattened to plain RGB and saved again.
    """
    file_format = Image.registered_extensions().get(target.suffix.lower())
    if file_format is None:
        raise ValueError(f"cannot tell which image format {target.suffix!r} is")
    params: dict[str, Any] = {key: image.info[key] for key in PRESERVED_INFO if key in image.info}
    params["quality"] = quality
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + _PARTIAL_SUFFIX)
    try:
        try:
            image.save(partial, format=file_format, **params)
        except (OSError, ValueError):
            if image.mode in ("RGB", "L"):
                raise
            flat = image.convert("RGB")
            params.pop("transparency", None)
            flat.save(partial, format=file_format, **params)
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)


def crop_file(source: Path, target: Path, op: CropOp, *, quality: int = DEFAULT_QUALITY) -> None:
    """Read ``source``, crop it as ``op`` says and write the result to ``target``."""
    image = open_upright(source)
    save(apply(image, op), target, quality=quality)
