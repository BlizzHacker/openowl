"""Visual diff tools -- before/after screenshot comparison.

Provides:
    screenshot_baseline  - capture a reference screenshot
    screenshot_diff      - capture current screen and highlight changes vs baseline

The diff overlay paints changed pixels in semi-transparent red and draws
a bounding box around the changed region.  Useful for verifying that a
code change produced the expected visual result.
"""

import base64
import io
import time
from typing import Optional

from PIL import Image, ImageChops, ImageDraw

from openowl.tools.screenshot import (
    capture_screenshot,
    _b64_to_image,
    _build_region,
)

# ------------------------------------------------------------------
# Module state
# ------------------------------------------------------------------

_baseline: Optional[dict] = None  # {b64, width, height, monitor, region, timestamp}


# ------------------------------------------------------------------
# Core algorithm
# ------------------------------------------------------------------

def compute_visual_diff(
    baseline_b64: str,
    current_b64: str,
    threshold: float = 0.02,
    highlight_color: tuple[int, int, int, int] = (255, 0, 0, 100),
) -> dict:
    """Compare two base64-encoded images and produce a diff overlay.

    Parameters
    ----------
    baseline_b64 : str
        Base64-encoded baseline image.
    current_b64 : str
        Base64-encoded current image.
    threshold : float
        Per-pixel diff threshold in [0, 1].  Pixels whose max-channel
        normalised difference exceeds this are marked as changed.
    highlight_color : tuple
        RGBA color for the changed-pixel overlay.

    Returns
    -------
    dict
        ``{"overlay_b64": str, "changed_fraction": float,
          "is_identical": bool, "bbox": tuple|None,
          "current_b64": str}``
        *bbox* is ``(x, y, w, h)`` of the tightest rectangle around all
        changed pixels, or ``None`` when identical.
    """
    baseline_img = _b64_to_image(baseline_b64)
    current_img_rgb = _b64_to_image(current_b64)

    # Size mismatch -> treat as fully different
    if baseline_img.size != current_img_rgb.size:
        raise ValueError(
            f"Resolution mismatch: baseline {baseline_img.size} "
            f"vs current {current_img_rgb.size}"
        )

    # Per-pixel diff using Pillow
    diff = ImageChops.difference(baseline_img, current_img_rgb)

    # Threshold: convert to grayscale (max channel approx via lighter blend),
    # then point-filter to create a binary mask
    # Use the L channel of each color diff to get max-channel behaviour
    r, g, b = diff.split()
    # Max across channels: composite via lighter
    channel_max = ImageChops.lighter(ImageChops.lighter(r, g), b)
    threshold_int = int(threshold * 255)
    mask = channel_max.point(lambda px: 255 if px > threshold_int else 0, mode="L")

    # Compute changed fraction
    mask_pixels = list(mask.getdata())
    total = len(mask_pixels)
    changed_count = sum(1 for px in mask_pixels if px > 0)
    changed_fraction = changed_count / total if total > 0 else 0.0
    is_identical = changed_count == 0

    # Build overlay image: current screenshot + semi-transparent highlight
    current_img = current_img_rgb.convert("RGBA")
    overlay = Image.new("RGBA", current_img.size, (0, 0, 0, 0))

    # Paint changed pixels with highlight color using mask
    highlight = Image.new("RGBA", current_img.size, highlight_color)
    overlay = Image.composite(highlight, overlay, mask)

    bbox = None
    if not is_identical:
        # Compute bounding box of changed region
        raw_bbox = mask.getbbox()  # returns (x_min, y_min, x_max, y_max)
        if raw_bbox:
            x_min, y_min, x_max, y_max = raw_bbox
            bbox = (x_min, y_min, x_max - x_min, y_max - y_min)

            # Draw bounding box rectangle on overlay
            draw = ImageDraw.Draw(overlay)
            draw.rectangle(
                [(x_min, y_min), (x_max - 1, y_max - 1)],
                outline=(255, 0, 0, 200),
                width=2,
            )

    # Composite overlay onto current image
    composited = Image.alpha_composite(current_img, overlay)

    # Encode result as JPEG
    composited_rgb = composited.convert("RGB")
    buf = io.BytesIO()
    composited_rgb.save(buf, format="JPEG", quality=85)
    overlay_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    return {
        "overlay_b64": overlay_b64,
        "changed_fraction": changed_fraction,
        "is_identical": is_identical,
        "bbox": bbox,
        "current_b64": current_b64,
    }


# ------------------------------------------------------------------
# MCP tool registration
# ------------------------------------------------------------------

def register(server) -> int:
    """Register *screenshot_baseline* and *screenshot_diff* tools.

    Returns the number of tools registered (2).
    """
    from mcp.server.fastmcp import Image as McpImage
    from openowl.tools.safety import with_timeout, ActionTimeoutError

    @server.tool()
    def screenshot_baseline(
        monitor: int = 0,
        region_x: int | None = None,
        region_y: int | None = None,
        region_w: int | None = None,
        region_h: int | None = None,
    ) -> list:
        """Capture a baseline screenshot for later visual comparison.

        Call this before making a change, then use screenshot_diff after
        the change to see exactly what moved.  Uses the same monitor/region
        params as screenshot.
        """
        global _baseline
        region = _build_region(region_x, region_y, region_w, region_h)

        try:
            result = with_timeout(
                lambda: capture_screenshot(
                    monitor_index=monitor if monitor != 0 else None,
                    region=region,
                ),
                timeout=5.0,
            )
        except ActionTimeoutError:
            return "Timed out after 5s capturing baseline. Display may be unresponsive."

        _baseline = {
            "b64": result["image"],
            "width": result["width"],
            "height": result["height"],
            "monitor": monitor,
            "region": region,
            "timestamp": time.time(),
        }

        return [
            McpImage(data=base64.b64decode(result["image"]), format="png"),
            f"Baseline captured: {result['width']}x{result['height']}. "
            f"Now make your change, then call screenshot_diff to compare.",
        ]

    @server.tool()
    def screenshot_diff(
        threshold: float = 0.02,
    ) -> list:
        """Compare the current screen against the stored baseline.

        Returns an overlay image highlighting changed pixels in red, with
        a bounding box around the changed region.  Call screenshot_baseline
        first to set the reference.

        Parameters:
            threshold: Pixel diff sensitivity (0.0-1.0). Lower = more sensitive.
                       Default 0.02 catches subtle changes without noise.
        """
        global _baseline

        if _baseline is None:
            return "No baseline set. Call screenshot_baseline first."

        # Recapture with same monitor/region as baseline
        region = _baseline["region"]
        monitor = _baseline["monitor"]

        try:
            result = with_timeout(
                lambda: capture_screenshot(
                    monitor_index=monitor if monitor != 0 else None,
                    region=region,
                ),
                timeout=5.0,
            )
        except ActionTimeoutError:
            return "Timed out after 5s capturing current screenshot."

        # Compute diff
        try:
            diff = compute_visual_diff(
                _baseline["b64"],
                result["image"],
                threshold=threshold,
            )
        except ValueError as e:
            return f"Diff failed: {e}"

        if diff["is_identical"]:
            return [
                McpImage(data=base64.b64decode(result["image"]), format="png"),
                "No changes detected. Screen is identical to baseline.",
            ]

        pct = diff["changed_fraction"] * 100
        bbox = diff["bbox"]
        summary = f"Changed: {pct:.1f}%"
        if bbox:
            summary += f" — bounding box at ({bbox[0]},{bbox[1]}) {bbox[2]}x{bbox[3]}"

        return [
            McpImage(data=base64.b64decode(diff["overlay_b64"]), format="png"),
            summary,
        ]

    return 2

