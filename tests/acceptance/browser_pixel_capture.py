"""Periodic real pixels in a fixed, conservative caption region."""
import hashlib
import math
from pathlib import Path
import struct


SHADOW_MARGIN_CSS_PX = 8
PIXEL_STATE = r"""() => {
  const caption = document.getElementById('current');
  const player = document.getElementById('playerWrap');
  const started = window.__realAcceptanceSampler?.started;
  if (!caption || !player || !Number.isFinite(started)) return null;
  const rect = value => ({x: value.x, y: value.y, width: value.width, height: value.height});
  const bounds = player.getBoundingClientRect();
  const css = getComputedStyle(caption);
  const rawBottom = css.bottom.endsWith('%') ? parseFloat(css.bottom) * bounds.height / 100 : parseFloat(css.bottom);
  // Computed CSS switches from percentages to rounded px when the caption appears.
  const bottom = Math.round(rawBottom * 64) / 64;
  const font = {line_height: parseFloat(css.lineHeight), padding_top: parseFloat(css.paddingTop),
    padding_bottom: parseFloat(css.paddingBottom), border_top: parseFloat(css.borderTopWidth),
    border_bottom: parseFloat(css.borderBottomWidth), bottom,
    font_size: css.fontSize, font_family: css.fontFamily, font_weight: css.fontWeight,
    text_shadow: css.textShadow};
  if (![font.line_height, font.padding_top, font.padding_bottom, font.border_top,
        font.border_bottom, bottom].every(Number.isFinite)) return null;
  const height = 2 * font.line_height + font.padding_top + font.padding_bottom
    + font.border_top + font.border_bottom;
  const x = Math.floor(bounds.x);
  const y = Math.floor(bounds.bottom - bottom - height - 8);
  const captionRect = caption.getBoundingClientRect();
  const captionBounds = caption.getClientRects().length && captionRect.width > 0 && captionRect.height > 0
    ? rect(captionRect) : null;
  const text = caption.innerText || '';
  return {observed_after_ms: performance.now() - started,
    viewport: {width: innerWidth, height: innerHeight}, device_scale_factor: devicePixelRatio,
    player_bounds: rect(bounds), font_geometry: font,
    raw_caption_bottom_css: css.bottom, raw_caption_bottom_css_px: rawBottom,
    candidate_roi: {x, y, width: Math.ceil(bounds.right) - x,
      height: Math.ceil(bounds.bottom - bottom + 8) - y},
    caption_bounds: captionBounds, visible: !!text.trim() && captionBounds !== null,
    visible_text: text, content_clipped: captionBounds !== null
      && (caption.scrollWidth > caption.clientWidth + 1 || caption.scrollHeight > caption.clientHeight + 1)};
}"""


def _geometry_valid(snapshot, installation):
    if not isinstance(snapshot, dict):
        return False
    for key in ("viewport", "device_scale_factor", "player_bounds", "font_geometry"):
        if snapshot.get(key) != installation.get(key):
            return False
    roi = installation["fixed_roi"]
    viewport = snapshot["viewport"]
    if (not all(type(roi.get(key)) is int for key in ("x", "y", "width", "height"))
            or roi["x"] < 0 or roi["y"] < 0 or roi["width"] <= 0 or roi["height"] <= 0
            or roi["x"] + roi["width"] > viewport["width"]
            or roi["y"] + roi["height"] > viewport["height"] or snapshot.get("content_clipped")):
        return False
    box = snapshot.get("caption_bounds")
    if box:
        if not all(isinstance(box.get(key), (int, float)) and math.isfinite(box[key])
                   for key in ("x", "y", "width", "height")):
            return False
        margin = SHADOW_MARGIN_CSS_PX
        if (box["x"] - margin < roi["x"] or box["y"] - margin < roi["y"]
                or box["x"] + box["width"] + margin > roi["x"] + roi["width"]
                or box["y"] + box["height"] + margin > roi["y"] + roi["height"]):
            return False
    return True


def install_pixel_capture(page):
    """Freeze the region even when the initial caption is blank."""
    snapshot = page.evaluate(PIXEL_STATE)
    if not isinstance(snapshot, dict):
        raise ValueError("pixel_geometry_unknown")
    installation = {key: snapshot[key] for key in (
        "viewport", "device_scale_factor", "player_bounds", "font_geometry")}
    installation.update(fixed_roi=snapshot["candidate_roi"], shadow_margin_css_px=SHADOW_MARGIN_CSS_PX,
                        target_interval_ms=100, ledger_kind="pixel_observation", status="installed",
                        raw_caption_bottom_css=snapshot.get("raw_caption_bottom_css"),
                        raw_caption_bottom_css_px=snapshot.get("raw_caption_bottom_css_px"),
                        font_geometry_normalization={"bottom_css_pixel_quantum": 1 / 64,
                                                     "rule": "round_to_nearest"})
    if not _geometry_valid(snapshot, installation):
        raise ValueError("pixel_geometry_unknown")
    return installation


def capture_pixel_observation(page, output_dir, report_root, index, installation):
    """Save one real PNG and its browser-time brackets, including blank images."""
    before = page.evaluate(PIXEL_STATE)
    if not _geometry_valid(before, installation):
        raise ValueError("pixel_geometry_unknown")
    directory = Path(output_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"pixel-{index:06d}.png"
    if destination.exists():
        raise FileExistsError("refusing to overwrite engineering pixel evidence")
    roi = installation["fixed_roi"]
    page.screenshot(path=str(destination), clip=roi, scale="css")
    after = page.evaluate(PIXEL_STATE)
    if not _geometry_valid(after, installation):
        raise ValueError("pixel_geometry_unknown")
    raw = destination.read_bytes()
    if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("pixel_image_invalid")
    width, height = struct.unpack(">II", raw[16:24])
    if (width, height) != (roi["width"], roi["height"]):
        raise ValueError("pixel_image_geometry_invalid")
    start, end = before["observed_after_ms"], after["observed_after_ms"]
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in (start, end)) or end < start:
        raise ValueError("pixel_observation_clock_invalid")
    return {
        "kind": "pixel_observation", "observation_index": index,
        "path": destination.relative_to(Path(report_root).resolve()).as_posix(),
        "sha256": hashlib.sha256(raw).hexdigest(), "byte_count": len(raw),
        "observed_after_ms": (start + end) / 2, "uncertainty_ms": (end - start) / 2,
        "before_after_ms": start, "after_after_ms": end,
        "visible": after["visible"], "visible_text": after["visible_text"],
        "dom_state_stable": (before["visible"], before["visible_text"]) == (after["visible"], after["visible_text"]),
        "viewport_before": before["viewport"], "viewport_after": after["viewport"],
        "device_scale_factor_before": before["device_scale_factor"],
        "device_scale_factor_after": after["device_scale_factor"],
        "caption_bounds_before": before["caption_bounds"], "caption_bounds_after": after["caption_bounds"],
        "raw_caption_bottom_css_before": before.get("raw_caption_bottom_css"),
        "raw_caption_bottom_css_after": after.get("raw_caption_bottom_css"),
        "raw_caption_bottom_css_px_before": before.get("raw_caption_bottom_css_px"),
        "raw_caption_bottom_css_px_after": after.get("raw_caption_bottom_css_px"),
        "font_geometry_normalization": installation["font_geometry_normalization"],
        "roi_bounds": roi, "shadow_margin_css_px": SHADOW_MARGIN_CSS_PX,
        "ocr_crop_bounds": {"x": 0, "y": 0, "width": width, "height": height},
        "ocr_crop_source": "fixed_caption_roi", "screenshot_width_px": width, "screenshot_height_px": height,
    }
