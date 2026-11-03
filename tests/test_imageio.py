from __future__ import annotations

import io
from pathlib import Path

import pytest
from conftest import MakeImage, coded_image, origin_of
from PIL import Image, ImageCms

from batch_crop import imageio
from batch_crop.engine import CropOp
from batch_crop.geometry import Size

ORIENTATION = 0x0112
MAKE = 0x010F


class TestDiscover:
    def test_extensions_match_in_any_case_and_sort_naturally(self, tmp_path: Path, make_image: MakeImage) -> None:
        for name in ("img10.JPG", "img2.jpeg", "img1.Png", "notes.txt", "img3.webp"):
            if name.endswith(".txt"):
                (tmp_path / "in").mkdir(exist_ok=True)
                (tmp_path / "in" / name).write_text("hi")
            else:
                make_image(f"in/{name}", 8, 8)
        found = imageio.discover_images(tmp_path / "in")
        assert [path.name for path in found] == ["img1.Png", "img2.jpeg", "img10.JPG"]

    def test_issue_upper_case_only_folder_is_found(self, tmp_path: Path, make_image: MakeImage) -> None:
        # The original matched ".jpg" exactly, so a folder of camera files (PHOTO.JPG) looked empty.
        make_image("in/PHOTO.JPG", 8, 8)
        assert [path.name for path in imageio.discover_images(tmp_path / "in")] == ["PHOTO.JPG"]

    def test_extra_extensions(self, tmp_path: Path, make_image: MakeImage) -> None:
        make_image("in/a.webp", 8, 8)
        make_image("in/b.png", 8, 8)
        found = imageio.discover_images(tmp_path / "in", ["WEBP"])
        assert [path.name for path in found] == ["a.webp"]

    def test_recursive_skips_the_output_folder(self, tmp_path: Path, make_image: MakeImage) -> None:
        make_image("in/a.png", 8, 8)
        make_image("in/sub/b.png", 8, 8)
        make_image("in/out/a.png", 8, 8)  # results of an earlier run inside the input folder
        flat = imageio.discover_images(tmp_path / "in")
        deep = imageio.discover_images(tmp_path / "in", recursive=True, exclude=tmp_path / "in" / "out")
        assert [p.relative_to(tmp_path / "in").as_posix() for p in flat] == ["a.png"]
        assert [p.relative_to(tmp_path / "in").as_posix() for p in deep] == ["a.png", "sub/b.png"]

    def test_output_folder_equal_to_input_is_not_excluded(self, tmp_path: Path, make_image: MakeImage) -> None:
        make_image("in/a.png", 8, 8)
        assert len(imageio.discover_images(tmp_path / "in", exclude=tmp_path / "in")) == 1


@pytest.mark.parametrize("name", ["照片 01.jpg", "スクリーンショット.png", "café/ünïcödé.jpeg"])
def test_non_ascii_paths(tmp_path: Path, make_image: MakeImage, name: str) -> None:
    # cv2.imread returns None for such paths on Windows; Pillow opens them everywhere.
    source = make_image(f"in/{name}", 120, 90)
    target = tmp_path / "出力" / Path(name).name
    imageio.crop_file(source, target, CropOp((10, 20, 60, 70)))
    with Image.open(target) as image:
        assert image.size == (50, 50)


def test_exif_icc_and_dpi_survive(tmp_path: Path) -> None:
    exif = Image.Exif()
    exif[MAKE] = "TestCam"
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    source = tmp_path / "photo.jpg"
    coded_image(120, 90).save(source, exif=exif.tobytes(), icc_profile=icc, dpi=(300, 300))
    target = tmp_path / "out" / "photo.jpg"
    imageio.crop_file(source, target, CropOp((0, 0, 50, 40)))
    with Image.open(target) as image:
        assert image.getexif().get(MAKE) == "TestCam"
        assert image.info.get("icc_profile") == icc
        assert tuple(round(value) for value in image.info["dpi"]) == (300, 300)


def test_exif_rotation_is_applied_like_a_photo_viewer(tmp_path: Path) -> None:
    exif = Image.Exif()
    exif[ORIENTATION] = 6  # stored sideways: viewers turn it 90 degrees clockwise
    source = tmp_path / "phone.jpg"
    coded_image(160, 90).save(source, exif=exif.tobytes(), quality=100)
    assert imageio.image_size(source) == Size(90, 160)
    target = tmp_path / "out.jpg"
    imageio.crop_file(source, target, CropOp((0, 0, 90, 80)))
    with Image.open(target) as image:
        assert image.size == (90, 80)
        assert image.getexif().get(ORIENTATION, 1) == 1  # already upright, must not be turned again


def test_transparency_and_bit_depth_are_kept(tmp_path: Path) -> None:
    rgba = coded_image(64, 64).convert("RGBA")
    rgba.putalpha(Image.new("L", (64, 64), 0))
    rgba.paste((255, 0, 0, 255), (16, 16, 48, 48))
    rgba.save(tmp_path / "logo.png")
    deep = Image.new("I;16", (64, 64), 40000)
    deep.save(tmp_path / "deep.png")

    imageio.crop_file(tmp_path / "logo.png", tmp_path / "out" / "logo.png", CropOp((8, 8, 40, 40)))
    imageio.crop_file(tmp_path / "deep.png", tmp_path / "out" / "deep.png", CropOp((0, 0, 10, 10)))
    with Image.open(tmp_path / "out" / "logo.png") as logo:
        assert logo.mode == "RGBA"
        assert logo.getpixel((0, 0))[3] == 0  # still transparent
        assert logo.getpixel((20, 20)) == (255, 0, 0, 255)
    with Image.open(tmp_path / "out" / "deep.png") as image:
        assert image.mode.startswith("I")
        assert image.getpixel((0, 0)) == 40000


def test_palette_transparency_is_kept(tmp_path: Path) -> None:
    palette = coded_image(32, 32).convert("P")
    palette.info["transparency"] = 0
    palette.save(tmp_path / "icon.png", transparency=0)
    imageio.crop_file(tmp_path / "icon.png", tmp_path / "out.png", CropOp((0, 0, 16, 16)))
    with Image.open(tmp_path / "out.png") as image:
        assert image.mode == "P"
        assert image.info.get("transparency") == 0


def test_transparent_picture_saved_as_jpeg_is_flattened(tmp_path: Path) -> None:
    # A PNG that someone named .jpg: JPEG cannot hold transparency, so the crop falls back to RGB.
    buffer = io.BytesIO()
    coded_image(40, 40).convert("RGBA").save(buffer, format="PNG")
    source = tmp_path / "misnamed.jpg"
    source.write_bytes(buffer.getvalue())
    imageio.crop_file(source, tmp_path / "out.jpg", CropOp((0, 0, 20, 20)))
    with Image.open(tmp_path / "out.jpg") as image:
        assert (image.format, image.mode, image.size) == ("JPEG", "RGB", (20, 20))


def test_format_follows_the_target_extension(tmp_path: Path) -> None:
    # Two demo pictures in img/input are WebP files named .jpg; the crop is a real JPEG, as before.
    source = tmp_path / "demo.jpg"
    coded_image(40, 40).save(source, format="WEBP")
    imageio.crop_file(source, tmp_path / "out" / "demo.jpg", CropOp((0, 0, 20, 20)))
    with Image.open(tmp_path / "out" / "demo.jpg") as image:
        assert image.format == "JPEG"


def test_quality_changes_jpeg_size(tmp_path: Path) -> None:
    source = tmp_path / "a.png"
    coded_image(256, 256).save(source)
    op = CropOp((0, 0, 256, 256))
    imageio.crop_file(source, tmp_path / "high.jpg", op, quality=95)
    imageio.crop_file(source, tmp_path / "low.jpg", op, quality=20)
    assert (tmp_path / "low.jpg").stat().st_size < (tmp_path / "high.jpg").stat().st_size


def test_stretch_output_has_the_box_size(tmp_path: Path) -> None:
    source = tmp_path / "wide.png"
    coded_image(800, 450).save(source)
    imageio.crop_file(source, tmp_path / "out.png", CropOp((200.0, 60.0, 600.0, 210.0), output_size=Size(200, 100)))
    with Image.open(tmp_path / "out.png") as image:
        assert image.size == (200, 100)


def test_failed_save_leaves_no_partial_file(tmp_path: Path) -> None:
    image = coded_image(10, 10)
    with pytest.raises(ValueError, match="which image format"):
        imageio.save(image, tmp_path / "out.unknown-ext")
    assert list(tmp_path.iterdir()) == []


def test_crop_matches_the_pixels(tmp_path: Path) -> None:
    source = tmp_path / "a.png"
    coded_image(300, 200).save(source)
    imageio.crop_file(source, tmp_path / "out.png", CropOp((37, 11, 137, 111)))
    with Image.open(tmp_path / "out.png") as image:
        assert origin_of(image) == (37, 11)


@pytest.mark.parametrize(("ext", "expected"), [("JPG", ".jpg"), (".Png", ".png"), (" webp ", ".webp")])
def test_normalize_extension(ext: str, expected: str) -> None:
    assert imageio.normalize_extension(ext) == expected
    assert expected in imageio.known_extensions()
