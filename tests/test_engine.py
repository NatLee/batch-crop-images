from __future__ import annotations

from pathlib import Path

import pytest
from conftest import MakeImage, origin_of
from PIL import Image

from batch_crop.engine import (
    Aborted,
    CropError,
    CropOp,
    CropSpec,
    Job,
    Mismatch,
    Status,
    operation_for,
    plan,
    run,
)
from batch_crop.geometry import Anchor, Box, Size

SPEC = CropSpec(Box(100, 40, 200, 100), Size(400, 300))


def never(path: Path, size: Size, reference: Size) -> Mismatch:
    raise AssertionError(f"{path} has the reference size; nobody should be asked about it")


class TestOperationFor:
    def test_reference_size_gets_the_box_as_drawn_whatever_the_mode(self) -> None:
        for mode in (None, *Mismatch):
            assert operation_for(SPEC, Size(400, 300), mode) == CropOp((100, 40, 300, 140))

    def test_stretch_resamples_the_matching_region_to_the_box_size(self) -> None:
        # The original behaviour: resize to the first image's size, then crop.
        op = operation_for(SPEC, Size(800, 450), Mismatch.STRETCH)
        assert op == CropOp((200.0, 60.0, 600.0, 210.0), output_size=Size(200, 100))
        assert op.result_size == Size(200, 100)

    def test_scale_takes_the_same_part_without_resampling(self) -> None:
        op = operation_for(SPEC, Size(800, 450), Mismatch.SCALE)
        assert op == CropOp((200, 60, 600, 210))
        assert op is not None and op.result_size == Size(400, 150)

    @pytest.mark.parametrize(
        ("anchor", "origin"),
        [
            (Anchor.TOP_LEFT, (100, 40)),
            (Anchor.CENTER, (300, 115)),
            (Anchor.BOTTOM_RIGHT, (500, 190)),
            (Anchor.TOP_RIGHT, (500, 40)),
        ],
    )
    def test_anchor_keeps_pixel_size_and_distance_to_the_anchor(self, anchor: Anchor, origin: tuple[int, int]) -> None:
        op = operation_for(SPEC, Size(800, 450), Mismatch.ANCHOR, anchor)
        x, y = origin
        assert op == CropOp((x, y, x + 200, y + 100))

    def test_anchor_on_a_smaller_image_that_cannot_hold_the_box(self) -> None:
        with pytest.raises(CropError, match="does not fit"):
            operation_for(SPEC, Size(150, 300), Mismatch.ANCHOR, Anchor.TOP_LEFT)

    def test_skip(self) -> None:
        assert operation_for(SPEC, Size(800, 450), Mismatch.SKIP) is None

    def test_ask_must_be_decided_first(self) -> None:
        with pytest.raises(ValueError, match="decided"):
            operation_for(SPEC, Size(800, 450), Mismatch.ASK)

    def test_scale_down_to_nothing_is_a_crop_error(self) -> None:
        tiny = CropSpec(Box(0, 0, 1, 1), Size(400, 300))
        with pytest.raises(CropError, match="too small"):
            operation_for(tiny, Size(40, 30), Mismatch.SCALE)


def test_spec_box_must_fit_the_reference() -> None:
    with pytest.raises(CropError, match="does not fit"):
        CropSpec(Box(300, 0, 200, 100), Size(400, 300))


class TestPlan:
    def test_targets_mirror_sub_folders(self, tmp_path: Path, make_image: MakeImage) -> None:
        sources = [make_image("in/a.png", 400, 300), make_image("in/sub/b.png", 400, 300)]
        jobs = plan(sources, input_dir=tmp_path / "in", output_dir=tmp_path / "out", spec=SPEC, decide=never)
        assert [job.target for job in jobs] == [tmp_path / "out" / "a.png", tmp_path / "out" / "sub" / "b.png"]
        assert all(job.will_write and not job.note for job in jobs)

    def test_only_other_sizes_are_decided(self, tmp_path: Path, make_image: MakeImage) -> None:
        asked: list[tuple[str, Size, Size]] = []

        def decide(path: Path, size: Size, reference: Size) -> Mismatch:
            asked.append((path.name, size, reference))
            return Mismatch.STRETCH

        sources = [make_image("in/a.png", 400, 300), make_image("in/b.png", 800, 450)]
        jobs = plan(sources, input_dir=tmp_path / "in", output_dir=tmp_path / "out", spec=SPEC, decide=decide)
        assert asked == [("b.png", Size(800, 450), Size(400, 300))]
        assert jobs[1].note == "stretched from 800x450 to 400x300"

    def test_abort_stops_planning(self, tmp_path: Path, make_image: MakeImage) -> None:
        def refuse(path: Path, size: Size, reference: Size) -> Mismatch:
            raise Aborted("no")

        sources = [make_image("in/a.png", 800, 450)]
        with pytest.raises(Aborted):
            plan(sources, input_dir=tmp_path / "in", output_dir=tmp_path / "out", spec=SPEC, decide=refuse)

    def test_existing_targets_can_be_kept(self, tmp_path: Path, make_image: MakeImage) -> None:
        sources = [make_image("in/a.png", 400, 300), make_image("in/b.png", 400, 300)]
        make_image("out/a.png", 10, 10)
        jobs = plan(
            sources, input_dir=tmp_path / "in", output_dir=tmp_path / "out", spec=SPEC, decide=never, skip_existing=True
        )
        assert [(job.will_write, job.note) for job in jobs] == [(False, "already exists"), (True, "")]

    def test_unreadable_files_and_boxes_that_do_not_fit_become_errors(
        self, tmp_path: Path, make_image: MakeImage
    ) -> None:
        broken = tmp_path / "in" / "broken.png"
        broken.parent.mkdir(parents=True)
        broken.write_bytes(b"not a picture")
        small = make_image("in/small.png", 150, 100)

        def anchor(path: Path, size: Size, reference: Size) -> Mismatch:
            return Mismatch.ANCHOR

        jobs = plan([broken, small], input_dir=tmp_path / "in", output_dir=tmp_path / "out", spec=SPEC, decide=anchor)
        assert jobs[0].error.startswith("cannot read image")
        assert "does not fit" in jobs[1].error
        assert not any(job.will_write for job in jobs)


class TestRun:
    def _jobs(self, tmp_path: Path, make_image: MakeImage, count: int) -> list[Job]:
        sources = [make_image(f"in/{index}.png", 400, 300) for index in range(count)]
        return plan(sources, input_dir=tmp_path / "in", output_dir=tmp_path / "out", spec=SPEC, decide=never)

    def test_writes_every_crop(self, tmp_path: Path, make_image: MakeImage) -> None:
        results = run(self._jobs(tmp_path, make_image, 3))
        assert [result.status for result in results] == [Status.WRITTEN] * 3
        for result in results:
            with Image.open(result.job.target) as image:
                assert image.size == (200, 100)
                assert origin_of(image) == (100, 40)

    def test_parallel_and_serial_runs_agree_and_keep_plan_order(self, tmp_path: Path, make_image: MakeImage) -> None:
        jobs = self._jobs(tmp_path, make_image, 8)
        seen: list[Path] = []
        results = run(jobs, workers=4, on_result=lambda result: seen.append(result.job.source))
        assert seen == [job.source for job in jobs]
        assert [result.job for result in results] == jobs
        assert all(result.status is Status.WRITTEN for result in results)

    def test_dry_run_writes_nothing(self, tmp_path: Path, make_image: MakeImage) -> None:
        results = run(self._jobs(tmp_path, make_image, 2), dry_run=True)
        assert [result.status for result in results] == [Status.PLANNED] * 2
        assert not (tmp_path / "out").exists()

    def test_errors_and_skips_are_reported_not_raised(self, tmp_path: Path) -> None:
        jobs = [
            Job(tmp_path / "a.png", tmp_path / "out" / "a.png", error="cannot read image: boom"),
            Job(tmp_path / "b.png", tmp_path / "out" / "b.png", note="already exists"),
            Job(tmp_path / "missing.png", tmp_path / "out" / "c.png", CropOp((0, 0, 1, 1))),
        ]
        results = run(jobs, workers=1)
        assert [result.status for result in results] == [Status.FAILED, Status.SKIPPED, Status.FAILED]
        assert results[0].message == "cannot read image: boom"
