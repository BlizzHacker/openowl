"""Vision tools -- screen capture and wait-for-change detection.

Provides:
    capture_screenshot  - grab the screen (or a region) via mss
    crop_region         - crop a PIL Image to {x, y, w, h}
    wait_for_change     - poll until the screen changes or timeout
    register            - wire the above as MCP tools on a Server
"""

import base64
import io
import time
from typing import Optional

import mss
from PIL import Image, ImageChops, ImageStat

from openowl.screenshot_manager import ScreenshotManager

# Lazy-initialised mss instance (reused across calls).
_sct: Optional[mss.mss] = None

# The screenshot manager is injected at import time from server.py.
# For tests, callers can mock this attribute directly.
screenshot_manager: Optional[ScreenshotManager] = None

# Max width for screenshots sent to Claude. Images wider than this are
# downscaled proportionally.  1280px is the sweet spot: all UI text,
# buttons, and labels remain readable while cutting payload ~17x vs
# raw 5K PNGs (119KB JPEG vs ~2MB PNG).
MAX_SCREENSHOT_WIDTH = 1280

# Last screenshot's scale factor (original_width / transport_width).
# Used by click() to auto-convert screenshot-space coords to screen-space.
_last_screenshot_scale: float = 1.0


# ------------------------------------------------------------------
# DPI scaling
# ------------------------------------------------------------------

# Cached DPI scale (queried once at first use)
_dpi_scale: Optional[float] = None


def _query_dpi() -> float:
    """Query the primary monitor's DPI scale factor via platform backend."""
    try:
        from openowl.owl_platform import get_dpi_scale as _plat_dpi
        return _plat_dpi()
    except (ImportError, NotImplementedError):
        return 1.0


def get_dpi_scale() -> float:
    """Get the cached DPI scale factor."""
    global _dpi_scale
    if _dpi_scale is None:
        _dpi_scale = _query_dpi()
    return _dpi_scale


def get_screenshot_scale() -> float:
    """Get the last screenshot's downscale factor (original / transport).

    Used by click() to auto-convert coordinates from screenshot-space
    (what Claude sees) to screen-space (what pyautogui needs).
    Returns 1.0 if no downscaling occurred.
    """
    return _last_screenshot_scale


def physical_to_logical(x: int, y: int, scale: Optional[float] = None) -> tuple[int, int]:
    """Convert physical (screenshot) pixels to logical (input) pixels."""
    if scale is None:
        scale = get_dpi_scale()
    if scale == 1.0:
        return (x, y)
    return (int(x / scale), int(y / scale))


def logical_to_physical(x: int, y: int, scale: Optional[float] = None) -> tuple[int, int]:
    """Convert logical (input) pixels to physical (screenshot) pixels."""
    if scale is None:
        scale = get_dpi_scale()
    if scale == 1.0:
        return (x, y)
    return (int(x * scale), int(y * scale))


# ------------------------------------------------------------------
# Helper: region assembly
# ------------------------------------------------------------------

def _build_region(
    x: Optional[int],
    y: Optional[int],
    w: Optional[int],
    h: Optional[int],
) -> Optional[dict]:
    """Build a region dict from individual x/y/w/h params.

    Returns None if any parameter is None.
    """
    if x is None or y is None or w is None or h is None:
        return None
    return {"x": x, "y": y, "w": w, "h": h}


# ------------------------------------------------------------------
# Core functions
# ------------------------------------------------------------------

def crop_region(img: Image.Image, region: dict) -> Image.Image:
    """Crop *img* to the rectangle described by *region*.

    Parameters
    ----------
    img : PIL.Image.Image
        Source image.
    region : dict
        ``{"x": int, "y": int, "w": int, "h": int}``

    Returns
    -------
    PIL.Image.Image
        The cropped image.
    """
    x, y, w, h = region["x"], region["y"], region["w"], region["h"]
    return img.crop((x, y, x + w, y + h))


def capture_screenshot(
    monitor_index: Optional[int] = None,
    region: Optional[dict] = None,
) -> dict:
    """Capture the screen (or a region) and return metadata + base64 PNG.

    Parameters
    ----------
    monitor_index : int | None
        0 = all monitors combined, 1 = primary, 2 = second, etc.
        Defaults to 1 (primary) when None.
    region : dict | None
        ``{"x": int, "y": int, "w": int, "h": int}`` to crop after capture.

    Returns
    -------
    dict
        ``{"image": str, "width": int, "height": int, "path": str}``
    """
    global _sct

    if _sct is None:
        _sct = mss.mss()

    if monitor_index is None:
        monitor_index = 1

    try:
        monitor = _sct.monitors[monitor_index]
        shot = _sct.grab(monitor)
    except Exception:
        # Stale DC handle (e.g. after MCP reconnect) — recreate
        _sct = mss.mss()
        monitor = _sct.monitors[monitor_index]
        shot = _sct.grab(monitor)

    # Convert mss screenshot to PIL Image
    img = Image.frombytes("RGB", shot.size, shot.rgb)

    # Optionally crop
    if region is not None:
        img = crop_region(img, region)

    # Record original dimensions before any downscaling
    original_width, original_height = img.size

    # Downscale if wider than MAX_SCREENSHOT_WIDTH
    if img.width > MAX_SCREENSHOT_WIDTH:
        scale = MAX_SCREENSHOT_WIDTH / img.width
        new_h = int(img.height * scale)
        img = img.resize((MAX_SCREENSHOT_WIDTH, new_h), Image.LANCZOS)

    # Encode as JPEG (much smaller than PNG for screenshots)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    png_bytes = buf.getvalue()  # name kept for compat

    # Save to disk via the screenshot manager
    path = screenshot_manager.save(png_bytes)

    # Base64 encode for MCP transport
    b64 = base64.b64encode(png_bytes).decode("ascii")

    # Track scale factor for auto-scaling in click()
    global _last_screenshot_scale
    if img.width != original_width:
        _last_screenshot_scale = original_width / img.width
    else:
        _last_screenshot_scale = 1.0

    return {
        "image": b64,
        "width": img.width,
        "height": img.height,
        "original_width": original_width,
        "original_height": original_height,
        "path": path,
        "dpi_scale": get_dpi_scale(),
    }


def wait_for_change(
    timeout: float = 5.0,
    threshold: float = 0.01,
    poll_interval: float = 0.5,
    region: Optional[dict] = None,
    monitor_index: Optional[int] = None,
) -> dict:
    """Poll the screen until it changes or *timeout* elapses.

    Captures a baseline screenshot, then repeatedly captures and compares.
    The comparison uses mean absolute pixel difference normalised to [0, 1].

    Parameters
    ----------
    timeout : float
        Maximum seconds to wait.
    threshold : float
        Minimum normalised diff to count as "changed".
    poll_interval : float
        Seconds between captures.
    region, monitor_index :
        Forwarded to :func:`capture_screenshot`.

    Returns
    -------
    dict
        ``{"changed": bool, "elapsed": float, "image": str,
          "width": int, "height": int, "path": str}``
    """
    cap_kwargs = {}
    if region is not None:
        cap_kwargs["region"] = region
    if monitor_index is not None:
        cap_kwargs["monitor_index"] = monitor_index

    baseline = capture_screenshot(**cap_kwargs)

    start = time.monotonic()
    latest = baseline

    while True:
        elapsed = time.monotonic() - start
        if elapsed >= timeout:
            return {
                "changed": False,
                "elapsed": elapsed,
                "image": latest["image"],
                "width": latest["width"],
                "height": latest["height"],
                "path": latest["path"],
            }

        time.sleep(poll_interval)

        current = capture_screenshot(**cap_kwargs)
        diff = compare_screenshots(baseline["image"], current["image"])

        latest = current

        if diff >= threshold:
            return {
                "changed": True,
                "elapsed": time.monotonic() - start,
                "image": current["image"],
                "width": current["width"],
                "height": current["height"],
                "path": current["path"],
            }


def get_screen_size() -> list:
    """Return dimensions of all available monitors.

    Index 0 is the combined virtual screen; 1+ are individual monitors.
    """
    global _sct
    if _sct is None:
        _sct = mss.mss()

    try:
        _ = _sct.monitors  # probe for stale handle
    except Exception:
        _sct = mss.mss()

    monitors = []
    for i, mon in enumerate(_sct.monitors):
        monitors.append({
            "index": i,
            "x": mon["left"],
            "y": mon["top"],
            "width": mon["width"],
            "height": mon["height"],
            "dpi_scale": get_dpi_scale(),
        })
    return monitors


# ------------------------------------------------------------------
# Annotation
# ------------------------------------------------------------------

def annotate_image(
    img: Image.Image,
    grid: bool = True,
    mouse_pos: Optional[tuple[int, int]] = None,
    elements: Optional[list[dict]] = None,
    grid_spacing: int = 500,
    scale: float = 1.0,
) -> Image.Image:
    """Draw annotation overlays on a copy of img.

    Parameters:
        img: Source screenshot (may be downscaled from original)
        grid: Draw grid lines with coordinate labels
        mouse_pos: (x, y) screen coordinates to draw crosshair at
        elements: List of element dicts with screen-space x, y, width, height
        grid_spacing: Pixels between grid lines in image space (default 500)
        scale: Downscale ratio (original_width / image_width). Grid labels
               show screen coordinates; elements and mouse are converted
               from screen-space to image-space for drawing.
    """
    from PIL import ImageDraw, ImageFont

    annotated = img.copy()
    draw = ImageDraw.Draw(annotated)

    try:
        font = ImageFont.truetype("arial.ttf", 14)
    except (IOError, OSError):
        font = ImageFont.load_default()

    w, h = img.size
    grid_color = (128, 128, 128, 128)  # Semi-transparent gray
    text_color = (200, 200, 200)

    if grid:
        # Vertical lines — labels show screen coordinates
        for x in range(0, w, grid_spacing):
            draw.line([(x, 0), (x, h)], fill=grid_color, width=1)
            draw.text((x + 2, 2), str(int(x * scale)), fill=text_color, font=font)
        # Horizontal lines
        for y in range(0, h, grid_spacing):
            draw.line([(0, y), (w, y)], fill=grid_color, width=1)
            draw.text((2, y + 2), str(int(y * scale)), fill=text_color, font=font)

    if mouse_pos:
        mx, my = mouse_pos
        # Convert screen coords to image coords for drawing
        draw_mx = int(mx / scale) if scale != 1.0 else mx
        draw_my = int(my / scale) if scale != 1.0 else my
        cross_size = 20
        cross_color = (255, 0, 0)  # Red
        draw.line([(draw_mx - cross_size, draw_my), (draw_mx + cross_size, draw_my)], fill=cross_color, width=2)
        draw.line([(draw_mx, draw_my - cross_size), (draw_mx, draw_my + cross_size)], fill=cross_color, width=2)
        # Label shows original screen coordinates (useful for clicking)
        draw.text((draw_mx + 5, draw_my + 5), f"({mx},{my})", fill=cross_color, font=font)

    if elements:
        elem_color = (0, 255, 128)  # Green
        for i, elem in enumerate(elements):
            # Convert screen-space coords to image-space for drawing
            x = int(elem["x"] / scale) if scale != 1.0 else elem["x"]
            y = int(elem["y"] / scale) if scale != 1.0 else elem["y"]
            r = int((elem["x"] + elem["width"]) / scale) if scale != 1.0 else elem["x"] + elem["width"]
            b = int((elem["y"] + elem["height"]) / scale) if scale != 1.0 else elem["y"] + elem["height"]
            draw.rectangle([(x, y), (r, b)], outline=elem_color, width=2)
            label = f"[{i}] {elem.get('role', '')} {elem.get('name', '')}"
            draw.text((x, y - 16), label, fill=elem_color, font=font)

    return annotated


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _b64_to_image(b64_str: str) -> Image.Image:
    """Decode a base64 PNG string to a PIL Image."""
    raw = base64.b64decode(b64_str)
    return Image.open(io.BytesIO(raw)).convert("RGB")


def compare_screenshots(before_b64: str, after_b64: str) -> float:
    """Normalised pixel diff [0.0, 1.0]. 0=identical, 1=max different."""
    before_img = _b64_to_image(before_b64)
    after_img = _b64_to_image(after_b64)
    if before_img.size != after_img.size:
        return 1.0
    diff = ImageChops.difference(before_img, after_img)
    stat = ImageStat.Stat(diff)
    # Mean across all channels, normalised to [0, 1]
    return sum(stat.mean) / (len(stat.mean) * 255.0)


# ------------------------------------------------------------------
# Accessibility helper
# ------------------------------------------------------------------

def _get_accessibility_summary(max_elements: int = 30) -> str:
    """Get a concise accessibility tree summary for the frontmost app.

    Returns formatted string of named elements with coordinates,
    or empty string if accessibility is unavailable.
    """
    try:
        from openowl.tools.ui_automation import do_list_elements
        result = do_list_elements(max_depth=3)
        elements = result.get("elements", [])
        # Filter to only named, useful elements
        useful = [e for e in elements if e.get("name", "").strip()
                  and e.get("role", "") not in ("Pane", "Group", "Custom", "")]
        if not useful:
            return ""
        lines = ["\nAccessible UI elements (use click_element for these):"]
        for i, e in enumerate(useful):
            cx = e["x"] + e["width"] // 2
            cy = e["y"] + e["height"] // 2
            lines.append(f'  [{i+1}] {e["role"]} "{e["name"]}" at ({cx},{cy})')
            if i >= max_elements - 1:
                lines.append(f"  ... and {len(useful) - max_elements} more")
                break
        return "\n".join(lines)
    except Exception:
        return ""


# ------------------------------------------------------------------
# MCP tool registration
# ------------------------------------------------------------------

def register(server) -> int:
    """Register *screenshot* and *wait_for_change* tools on *server*.

    Returns the number of tools registered (2).
    """
    from mcp.server.fastmcp import Image as McpImage

    from openowl.tools.safety import with_timeout, ActionTimeoutError

    @server.tool()
    def screenshot(
        monitor: int = 0,
        region_x: int | None = None,
        region_y: int | None = None,
        region_w: int | None = None,
        region_h: int | None = None,
        annotate: bool = False,
    ) -> list:
        """Capture a screenshot of the screen or a region.

        Returns the image so you can see what's on screen.
        Use monitor=0 for all monitors, monitor=1 for primary, etc.
        Optionally crop to a region with region_x/y/w/h.
        Set annotate=True to overlay grid lines, mouse position, and UI element outlines.
        """
        # Focus target window before capture so it's in the foreground
        try:
            from openowl.tools.target_window import ensure_focus, get_target
            if get_target():
                ensure_focus()
                import time; time.sleep(0.3)
        except Exception:
            pass
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
            return "Timed out after 5s capturing screenshot. Display may be unresponsive."

        if annotate:
            raw_img = Image.open(io.BytesIO(base64.b64decode(result["image"])))
            mouse_pos = None
            try:
                import pyautogui
                pos = pyautogui.position()
                mouse_pos = (pos[0], pos[1])
            except Exception:
                pass
            elements = None
            try:
                from openowl.tools.ui_automation import do_list_elements
                elem_result = do_list_elements(max_depth=2)
                elements = elem_result.get("elements", [])
            except Exception:
                pass
            focus_info = ""
            try:
                from openowl.tools.ui_automation import do_get_focused_element
                focus_result = do_get_focused_element()
                if focus_result["found"]:
                    fe = focus_result["element"]
                    focus_info = f" Focused: {fe['role']} \"{fe['name']}\" at ({fe['x']},{fe['y']})"
            except Exception:
                pass

            scale = result["original_width"] / result["width"]

            # Visual detect fallback: when UIA returns insufficient *useful*
            # elements, run OpenCV + OCR detection to find clickable regions.
            # Count only elements with names — unnamed Panes are useless.
            visual_detect_text = ""
            useful = [e for e in elements if e.get("name", "").strip()]
            if len(useful) < 3:
                try:
                    from openowl.tools.visual_detect import detect_ui_regions, format_regions_text
                    from openowl.tools.target_window import get_target

                    # Crop to target window bounds to avoid terminal bleed-through
                    detect_img = raw_img
                    crop_offset = (0, 0)
                    tgt = get_target()
                    if tgt:
                        try:
                            from openowl.tools.windows import do_list_windows
                            for w in do_list_windows():
                                if tgt.lower() in w["title"].lower():
                                    x0 = max(0, int(w["x"] / scale))
                                    y0 = max(0, int(w["y"] / scale))
                                    x1 = min(raw_img.width, int((w["x"] + w["width"]) / scale))
                                    y1 = min(raw_img.height, int((w["y"] + w["height"]) / scale))
                                    if x1 > x0 and y1 > y0:
                                        detect_img = raw_img.crop((x0, y0, x1, y1))
                                        crop_offset = (x0, y0)
                                    break
                        except Exception:
                            pass

                    regions = detect_ui_regions(detect_img, scale=scale)

                    # Offset regions back to full-image screen coords
                    if crop_offset != (0, 0):
                        ox, oy = crop_offset
                        for r in regions:
                            r["x"] += int(ox * scale)
                            r["y"] += int(oy * scale)

                    if regions:
                        visual_detect_text = format_regions_text(regions)
                except Exception:
                    pass

            annotated = annotate_image(raw_img, grid=True, mouse_pos=mouse_pos, elements=elements, scale=scale)
            buf = io.BytesIO()
            annotated.save(buf, format="PNG")
            if scale > 1.0:
                scale_hint = f" (scaled from {result['original_width']}x{result['original_height']}, click coordinates are auto-scaled)"
            else:
                scale_hint = ""

            return [
                McpImage(data=buf.getvalue(), format="png"),
                f"Annotated screenshot: {result['width']}x{result['height']} pixels{scale_hint}. File: {result['path']}{focus_info}{visual_detect_text}",
            ]

        # --- OCR text overlay (always-on for non-annotated screenshots) ---
        ocr_text = ""
        try:
            from openowl.tools.ocr import _run_ocr_parallel, _build_phrases, _deduplicate_words
            # Run normal + inverted OCR in parallel (saves ~0.5-1s)
            raw_words, inv_words = _run_ocr_parallel(result["path"])
            if inv_words:
                raw_words = _deduplicate_words(raw_words + inv_words)

            if raw_words:
                # Scale OCR coords from screenshot space to original screen space
                scale = result["original_width"] / result["width"]
                if scale != 1.0:
                    for w in raw_words:
                        w["x"] = round(w["x"] * scale)
                        w["y"] = round(w["y"] * scale)
                        w["width"] = round(w["width"] * scale)
                        w["height"] = round(w["height"] * scale)

                phrases = _build_phrases(raw_words)
                if phrases:
                    lines = ["\nText on screen (use these coordinates for click/click_text):"]
                    for i, p in enumerate(phrases):
                        cx = p["x"] + p["width"] // 2
                        cy = p["y"] + p["height"] // 2
                        lines.append(f'  [{i+1}] "{p["text"]}" at ({cx},{cy})')
                        if i >= 49:  # Cap at 50 entries
                            lines.append(f"  ... and {len(phrases) - 50} more")
                            break
                    ocr_text = "\n".join(lines)
        except Exception:
            pass  # OCR is best-effort, never block screenshot

        scale = result["original_width"] / result["width"]
        if scale > 1.0:
            scale_hint = f" (scaled from {result['original_width']}x{result['original_height']}, click coordinates are auto-scaled)"
        else:
            scale_hint = ""

        # --- Accessibility elements overlay ---
        a11y_text = _get_accessibility_summary()

        # --- Session context: what happened since last screenshot ---
        context_text = ""
        try:
            from openowl.tools.session_context import get_session
            session = get_session()
            context_text = session.get_change_summary()
            if context_text:
                context_text = "\n" + context_text
            session.record_screenshot(result["width"], result["height"])
        except Exception:
            pass

        return [
            McpImage(data=base64.b64decode(result["image"]), format="png"),
            f"Screenshot captured: {result['width']}x{result['height']} pixels{scale_hint}. File: {result['path']}{ocr_text}{a11y_text}{context_text}",
        ]

    @server.tool()
    def wait_for_change(
        timeout: float = 5.0,
        threshold: float = 0.01,
        poll_interval: float = 0.5,
        monitor: int = 0,
        region_x: int | None = None,
        region_y: int | None = None,
        region_w: int | None = None,
        region_h: int | None = None,
    ) -> list:
        """Wait until the screen content changes or timeout elapses.

        Use after clicking a button or triggering an action to wait for the
        UI to respond. Returns when change is detected or timeout expires.
        threshold is the fraction of pixels that must differ (0.01 = 1%).
        """
        from openowl.tools.screenshot import wait_for_change as _wait

        region = _build_region(region_x, region_y, region_w, region_h)
        try:
            result = with_timeout(
                lambda: _wait(
                    timeout=timeout,
                    threshold=threshold,
                    poll_interval=poll_interval,
                    region=region,
                    monitor_index=monitor if monitor != 0 else None,
                ),
                timeout=timeout + 5.0,
            )
        except ActionTimeoutError:
            return f"Timed out after {timeout + 5.0:.0f}s waiting for screen change. Display may be unresponsive."

        status = "changed" if result["changed"] else "unchanged"
        return [
            McpImage(data=base64.b64decode(result["image"]), format="png"),
            f"Screen {status} after {result['elapsed']:.1f}s. {result['width']}x{result['height']} pixels. File: {result['path']}",
        ]

    @server.tool()
    def get_screen_size() -> str:
        """Get the dimensions of all connected monitors.

        Returns monitor index, position (x, y), and size (width x height)
        for each display. Index 0 is the combined virtual screen spanning
        all monitors; index 1+ are individual monitors.
        """
        from openowl.tools.screenshot import get_screen_size as _get_size
        try:
            monitors = with_timeout(_get_size, timeout=3.0)
        except ActionTimeoutError:
            return "Timed out after 3s querying monitors."
        if not monitors:
            return "No monitors detected."
        lines = [f"Detected {len(monitors)} monitor entries:"]
        for m in monitors:
            label = "Combined" if m["index"] == 0 else f"Monitor {m['index']}"
            lines.append(
                f"  {label}: {m['width']}x{m['height']} at ({m['x']}, {m['y']})"
            )
        return "\n".join(lines)

    return 3

