import struct
import zlib

import pytest

from browser_pixel_capture import capture_pixel_observation, install_pixel_capture


def snapshot(*, caption=None, text="", at=10):
    return {"observed_after_ms": at, "viewport": {"width": 800, "height": 600},
            "device_scale_factor": 1, "player_bounds": {"x": 20, "y": 50, "width": 700, "height": 400},
            "font_geometry": {"line_height": 30, "padding_top": 4, "padding_bottom": 4,
                              "border_top": 0, "border_bottom": 0, "bottom": 40},
            "candidate_roi": {"x": 20, "y": 334, "width": 700, "height": 84},
            "caption_bounds": caption, "visible": bool(text), "visible_text": text,
            "content_clipped": False}


class PixelPage:
    def __init__(self, snapshots):
        self.snapshots = snapshots
        self.clips = []

    def evaluate(self, _script):
        return self.snapshots.pop(0)

    def screenshot(self, *, path, clip, scale):
        self.clips.append(clip)
        from pathlib import Path
        width, height = clip["width"], clip["height"]
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n"
                              + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                              + chunk(b"IDAT", zlib.compress((b"\0" * (width * 3 + 1)) * height))
                              + chunk(b"IEND", b""))


def test_periodic_blank_and_visible_pixels_keep_the_same_conservative_roi(tmp_path):
    box = {"x": 100, "y": 370, "width": 400, "height": 32}
    page = PixelPage([snapshot(), snapshot(at=20), snapshot(at=30),
                      snapshot(caption=box, text="Visible words", at=120),
                      snapshot(caption=box, text="Visible words", at=140)])
    installed = install_pixel_capture(page)
    blank = capture_pixel_observation(page, tmp_path, tmp_path, 1, installed)
    visible = capture_pixel_observation(page, tmp_path, tmp_path, 2, installed)

    assert blank["roi_bounds"] == visible["roi_bounds"] == page.clips[0] == page.clips[1]
    assert blank["ocr_crop_source"] == "fixed_caption_roi"
    assert blank["ocr_crop_bounds"] == {"x": 0, "y": 0, "width": 700, "height": 84}
    assert blank["observed_after_ms"] == 25
    assert blank["uncertainty_ms"] == 5
    assert visible["observed_after_ms"] == 130
    assert visible["uncertainty_ms"] == 10
    assert visible["shadow_margin_css_px"] == 8
    assert blank["visible"] is False and visible["visible_text"] == "Visible words"
    assert visible["font_geometry_normalization"] == {
        "bottom_css_pixel_quantum": 1 / 64, "rule": "round_to_nearest"}


def test_caption_outside_roi_is_unknown_before_cropping_any_glyphs(tmp_path):
    outside = snapshot(caption={"x": 10, "y": 370, "width": 400, "height": 32}, text="outside")
    page = PixelPage([snapshot(), outside])
    installed = install_pixel_capture(page)
    with pytest.raises(ValueError, match="pixel_geometry_unknown"):
        capture_pixel_observation(page, tmp_path, tmp_path, 1, installed)
    assert page.clips == []


def test_pixel_transition_during_screenshot_keeps_the_actual_image_and_bracket(tmp_path):
    box = {"x": 100, "y": 370, "width": 400, "height": 32}
    page = PixelPage([snapshot(), snapshot(at=20), snapshot(caption=box, text="new", at=40)])
    installed = install_pixel_capture(page)
    image = capture_pixel_observation(page, tmp_path, tmp_path, 1, installed)
    assert image["dom_state_stable"] is False
    assert image["before_after_ms"] == 20 and image["after_after_ms"] == 40
    assert image["caption_bounds_before"] is None and image["caption_bounds_after"] == box


@pytest.mark.parametrize("field, changed", [
    ("viewport", {"width": 801, "height": 600}),
    ("player_bounds", {"x": 20, "y": 50, "width": 700, "height": 401}),
    ("font_geometry", {"line_height": 31}),
])
def test_real_geometry_changes_remain_unknown(tmp_path, field, changed):
    altered = snapshot(at=20)
    altered[field] = changed
    page = PixelPage([snapshot(), altered])
    installed = install_pixel_capture(page)
    with pytest.raises(ValueError, match="pixel_geometry_unknown"):
        capture_pixel_observation(page, tmp_path, tmp_path, 1, installed)
    assert page.clips == []
