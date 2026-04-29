"""OCR tools -- find and click text on screen.

Fallback targeting for apps with poor accessibility trees.
Uses RapidOCR (rapidocr-onnxruntime) as the default engine on all platforms,
with macOS Vision as preferred when available on darwin.

Provides three MCP tools:
    find_text       - find text on screen, returns locations
    click_text      - find text + click Nth occurrence
    click_in_region - find text within a screen region + click it
"""

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from PIL import Image, ImageOps

from openowl.tools.input_tools import do_click

_OCR_MAX_DIM = 4096

# Shared thread pool for parallel OCR passes (2 threads: normal + inverted)
_ocr_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ocr")


def _get_window_rect(window_title: str) -> Optional[dict]:
    """Get a window's bounding rect for region-scoped OCR.

    Uses find_matching_window() for shared matching logic.
    Returns {"x": int, "y": int, "w": int, "h": int} or None if not found.
    Clamps negative coordinates to 0 (common for maximized windows).
    """
    from openowl.tools.windows import find_matching_window, do_list_windows
    all_windows = do_list_windows()
    result = find_matching_window(window_title, all_windows)
    if result["window"] is None:
        return None
    win = result["window"]
    return {
        "x": max(0, win["x"]),
        "y": max(0, win["y"]),
        "w": win["width"],
        "h": win["height"],
    }

# ---------------------------------------------------------------------------
# RapidOCR backend (required dependency)
# ---------------------------------------------------------------------------
_rapid_engine = None


def _get_rapid_engine():
    """Return the RapidOCR engine instance.

    Raises ImportError if rapidocr-onnxruntime is not installed.
    This is a required dependency -- callers should not swallow this error.
    """
    global _rapid_engine
    if _rapid_engine is not None:
        return _rapid_engine
    from rapidocr_onnxruntime import RapidOCR
    _rapid_engine = RapidOCR()
    print("[ocr] Using RapidOCR engine", file=sys.stderr)
    return _rapid_engine


def _rapid_ocr_on_array(arr, scale_factor: float = 1.0) -> list[dict]:
    """Run RapidOCR on a numpy array and return word dicts.

    Accepts a pre-loaded numpy array to avoid redundant disk I/O.
    scale_factor indicates if the array was downscaled (< 1.0 means coords
    need to be scaled back up to original resolution).
    """
    engine = _get_rapid_engine()

    try:
        results, _elapse = engine(arr)
    except Exception:
        return []

    if not results:
        return []

    # Convert line-level results to word-level dicts
    words = []
    min_confidence = 0.3
    for box_points, text, confidence in results:
        if confidence < min_confidence:
            continue
        xs = [p[0] for p in box_points]
        ys = [p[1] for p in box_points]
        line_x = round(min(xs))
        line_y = round(min(ys))
        line_w = round(max(xs) - min(xs))
        line_h = round(max(ys) - min(ys))

        parts = text.split()
        if not parts:
            continue
        if len(parts) == 1:
            words.append({
                "text": text.strip(),
                "x": line_x, "y": line_y,
                "width": line_w, "height": line_h,
            })
        else:
            total_chars = sum(len(p) for p in parts)
            if total_chars == 0:
                continue
            cursor_x = line_x
            for part in parts:
                frac = len(part) / total_chars
                part_w = round(line_w * frac)
                words.append({
                    "text": part,
                    "x": cursor_x, "y": line_y,
                    "width": part_w, "height": line_h,
                })
                cursor_x += part_w

    # Rescale coordinates back to original resolution
    if scale_factor < 1.0:
        inv = 1.0 / scale_factor
        for item in words:
            item["x"] = round(item["x"] * inv)
            item["y"] = round(item["y"] * inv)
            item["width"] = round(item["width"] * inv)
            item["height"] = round(item["height"] * inv)

    return words


def _rapid_ocr_on_image(image_path: str) -> list[dict]:
    """Run RapidOCR on an image file. Delegates to _rapid_ocr_on_array."""
    import numpy as np

    scale_factor = 1.0
    try:
        img = Image.open(image_path)
        w, h = img.size
        if max(w, h) > _OCR_MAX_DIM:
            scale_factor = _OCR_MAX_DIM / max(w, h)
            new_w = round(w * scale_factor)
            new_h = round(h * scale_factor)
            img = img.resize((new_w, new_h), Image.LANCZOS)
    except Exception:
        scale_factor = 1.0
        img = Image.open(image_path)

    arr = np.array(img)
    img.close()
    return _rapid_ocr_on_array(arr, scale_factor)


def _deduplicate_words(words: list[dict], tolerance: int = 5) -> list[dict]:
    """Remove duplicate OCR words (same text at similar coordinates)."""
    seen = []
    for w in words:
        is_dup = False
        for s in seen:
            if (s["text"] == w["text"]
                    and abs(s["x"] - w["x"]) <= tolerance
                    and abs(s["y"] - w["y"]) <= tolerance):
                is_dup = True
                break
        if not is_dup:
            seen.append(w)
    return seen


def _run_ocr(image_path: Optional[str] = None, invert: bool = False) -> list[dict]:
    """Run OCR on a screenshot. Dispatches to the best available engine.

    Optionally invert the image first (for dark backgrounds).
    In-memory inversion avoids temp file disk I/O.
    """
    if image_path is None:
        from openowl.tools.screenshot import capture_screenshot
        shot = capture_screenshot()
        image_path = shot["path"]

    if invert:
        try:
            import tempfile
            img = Image.open(image_path).convert("RGB")
            inverted = ImageOps.invert(img)

            # Downscale guard (same as normal path)
            w, h = inverted.size
            if max(w, h) > _OCR_MAX_DIM:
                scale = _OCR_MAX_DIM / max(w, h)
                inverted = inverted.resize(
                    (round(w * scale), round(h * scale)),
                    Image.LANCZOS,
                )

            # Save to temp file and run through the normal OCR engine
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            inverted.save(tmp, format="PNG")
            tmp.close()
            inverted.close()
            img.close()
            result = _run_ocr_engine(tmp.name)
            os.unlink(tmp.name)
            return result
        except Exception:
            return []

    return _run_ocr_engine(image_path)


def _run_ocr_engine(image_path: str) -> list[dict]:
    """Dispatch to Vision (macOS) or RapidOCR (Windows).

    macOS uses Apple Vision via PyObjC — no numpy needed.
    Windows uses RapidOCR which requires numpy (installed via venv).
    """
    try:
        from openowl.owl_platform import run_ocr_native
        result = run_ocr_native(image_path)
        if result:
            return result
    except (ImportError, Exception):
        pass
    # RapidOCR fallback (Windows only — needs numpy)
    if sys.platform != "darwin":
        return _rapid_ocr_on_image(image_path)
    return []


def _run_ocr_parallel(image_path: str) -> tuple[list[dict], list[dict]]:
    """Run normal and inverted OCR in parallel. Returns (normal_words, inverted_words)."""
    future_normal = _ocr_pool.submit(_run_ocr, image_path, False)
    future_inverted = _ocr_pool.submit(_run_ocr, image_path, True)
    normal_words = future_normal.result()
    inv_words = future_inverted.result()
    return normal_words, inv_words


def _merge_words(words: list[dict]) -> dict:
    """Merge a list of adjacent words into a single phrase dict."""
    text = " ".join(w["text"] for w in words)
    x = words[0]["x"]
    y = min(w["y"] for w in words)
    right = max(w["x"] + w["width"] for w in words)
    bottom = max(w["y"] + w["height"] for w in words)
    return {"text": text, "x": x, "y": y, "width": right - x, "height": bottom - y}


def _build_phrases(words: list[dict], line_tolerance: int = None, gap_tolerance: int = None) -> list[dict]:
    """Group adjacent OCR words into phrases.

    Words on the same line (Y within line_tolerance) and close together
    (gap between end of one word and start of next < gap_tolerance) are joined.
    Returns list of phrase dicts with merged bounding boxes.

    Tolerances default to adaptive values based on median word height, which
    scales naturally with font size and DPI.
    """
    if not words:
        return []

    # Adaptive defaults based on median word height
    heights = sorted(w["height"] for w in words)
    median_h = heights[len(heights) // 2] if heights else 20
    if line_tolerance is None:
        line_tolerance = max(10, median_h // 2)
    if gap_tolerance is None:
        gap_tolerance = max(30, int(median_h * 1.5))

    # Sort by Y then X
    sorted_words = sorted(words, key=lambda w: (w["y"], w["x"]))

    # Group into lines
    lines = []
    current_line = [sorted_words[0]]
    for w in sorted_words[1:]:
        if abs(w["y"] - current_line[0]["y"]) <= line_tolerance:
            current_line.append(w)
        else:
            lines.append(sorted(current_line, key=lambda w: w["x"]))
            current_line = [w]
    lines.append(sorted(current_line, key=lambda w: w["x"]))

    # Build phrases from each line
    phrases = []
    for line in lines:
        phrase_words = [line[0]]
        for w in line[1:]:
            prev = phrase_words[-1]
            prev_end = prev["x"] + prev["width"]
            gap = w["x"] - prev_end
            if gap <= gap_tolerance:
                phrase_words.append(w)
            else:
                phrases.append(_merge_words(phrase_words))
                phrase_words = [w]
        phrases.append(_merge_words(phrase_words))

    return phrases


def _search_words(words: list[dict], q: str, has_spaces: bool, case_sensitive: bool) -> list[dict]:
    """Search words/phrases for a query string."""
    matches = []
    if has_spaces:
        phrases = _build_phrases(words)
        for phrase in phrases:
            text = phrase["text"] if case_sensitive else phrase["text"].lower()
            if q in text:
                matches.append(phrase)
    else:
        for word in words:
            text = word["text"] if case_sensitive else word["text"].lower()
            if q in text:
                matches.append(word)
    return matches


def _capture_image_path() -> str:
    """Capture a screenshot and return its file path."""
    from openowl.tools.screenshot import capture_screenshot
    return capture_screenshot()["path"]


def _score_content_area(match: dict, window_rect: Optional[dict]) -> float:
    """Score an OCR match — lower is better (more likely content area).

    Penalises:
    - Top 150px of window (+200) — browser chrome / tabs / bookmarks
    - Left/right 15% of window width (+100) — sidebars
    - Y position as tiebreaker (0–10 range) — preserves top-to-bottom among equals
    """
    score = 0.0
    if window_rect is None:
        # No window context — fall back to y-position only
        return match["y"] / 100.0

    # Match position relative to window
    rel_y = match["y"] - window_rect["y"]
    rel_x = match["x"] - window_rect["x"]
    win_w = window_rect["w"]

    # Penalise top 150px (browser chrome / tabs / bookmarks bar)
    if rel_y < 150:
        score += 200

    # Penalise left/right 15% (sidebars)
    sidebar_px = win_w * 0.15
    if rel_x < sidebar_px or rel_x > (win_w - sidebar_px):
        score += 100

    # Y-position tiebreaker (0–10 range)
    score += min(10.0, rel_y / 100.0)

    return score


def do_find_text(
    query: str,
    case_sensitive: bool = False,
    image_path: Optional[str] = None,
    window_title: Optional[str] = None,
    prefer_content: bool = True,
    near_y: Optional[int] = None,
) -> dict:
    """Find text on screen via OCR. Retries with inverted image if query not found.

    When window_title is provided, OCR is scoped to that window's region and
    coordinates are offset back to screen-absolute.

    When prefer_content is True (default), matches are sorted by content-area
    score (deprioritising browser chrome and sidebars) instead of raw (y, x).
    """
    # Auto-scope to target window if no explicit window_title
    if window_title is None:
        from openowl.tools.target_window import get_target
        window_title = get_target()

    # Ensure target window is focused before OCR capture
    try:
        from openowl.tools.target_window import ensure_focus
        ensure_focus()
    except Exception:
        pass

    # Window-scoped region capture
    region_offset = None
    region_scale = 1.0  # screenshot may be downscaled for transport
    window_scope_warning = None
    _window_rect_for_scoring = None
    if image_path is None and window_title:
        rect = _get_window_rect(window_title)
        if rect:
            from openowl.tools.screenshot import capture_screenshot
            shot = capture_screenshot(region=rect)
            image_path = shot["path"]
            region_offset = (rect["x"], rect["y"])
            # Detect transport downscaling so OCR coords can be un-scaled
            if shot["width"] < rect["w"]:
                region_scale = rect["w"] / shot["width"]
            _window_rect_for_scoring = rect
        else:
            from openowl.tools.windows import do_list_windows
            available = [w["title"] for w in do_list_windows()]
            window_scope_warning = (
                f"No window matching '{window_title}'. "
                f"Available: {available}. Scanned full screen instead."
            )

    # Resolve image path once so both normal and inverted passes use the same screenshot
    if image_path is None:
        image_path = _capture_image_path()

    # Run normal and inverted OCR in parallel
    words, inv_words = _run_ocr_parallel(image_path)

    # Offset word coordinates from region-relative to screen-absolute before searching.
    # Un-scale first if the screenshot was downscaled for transport (e.g. 2219px → 1280px).
    # This ensures matches (which reference the same dicts) inherit the offset.
    if region_offset:
        ox, oy = region_offset
        for w in words:
            if region_scale != 1.0:
                w["x"] = round(w["x"] * region_scale)
                w["y"] = round(w["y"] * region_scale)
                w["width"] = round(w["width"] * region_scale)
                w["height"] = round(w["height"] * region_scale)
            w["x"] += ox
            w["y"] += oy

    # Merge inverted results (catches dark-background text)
    if inv_words:
        if region_offset:
            for w in inv_words:
                if region_scale != 1.0:
                    w["x"] = round(w["x"] * region_scale)
                    w["y"] = round(w["y"] * region_scale)
                    w["width"] = round(w["width"] * region_scale)
                    w["height"] = round(w["height"] * region_scale)
                w["x"] += ox
                w["y"] += oy
        merged = _deduplicate_words(words + inv_words)
        if len(merged) > len(words):
            words = merged

    q = query if case_sensitive else query.lower()
    has_spaces = " " in q
    matches = _search_words(words, q, has_spaces, case_sensitive)

    if prefer_content:
        matches.sort(key=lambda m: _score_content_area(m, _window_rect_for_scoring))
    else:
        matches.sort(key=lambda m: (m["y"], m["x"]))

    # Spatial disambiguation: re-sort by distance to a target Y coordinate
    if near_y is not None:
        matches.sort(key=lambda m: abs(m["y"] + m["height"] // 2 - near_y))

    result = {"matches": matches, "query": query, "total_words": len(words)}
    if window_scope_warning:
        result["window_scope_warning"] = window_scope_warning
    return result


def do_click_text(
    query: str,
    occurrence: int = 1,
    case_sensitive: bool = False,
    window_title: Optional[str] = None,
    prefer_content: bool = True,
    near_y: Optional[int] = None,
) -> dict:
    """Find text on screen and click the Nth occurrence.

    Self-correcting: if the initial click at the text center produces no
    visual change, automatically retries at offset positions around the
    text (below, above, right, left).  This handles cases where the
    interactive element is larger than or offset from its label text
    (e.g. input fields, padded buttons, wide menu rows).
    """
    result = do_find_text(query, case_sensitive=case_sensitive,
                          window_title=window_title, prefer_content=prefer_content,
                          near_y=near_y)

    if not result["matches"]:
        return {"success": False, "error": f"Text '{query}' not found on screen"}

    idx = min(occurrence - 1, len(result["matches"]) - 1)
    match = result["matches"][idx]
    center_x = match["x"] + match["width"] // 2
    center_y = match["y"] + match["height"] // 2

    # Try center click first
    click_result = do_click(center_x, center_y)

    out = {
        "success": True,
        "match": match,
        "clicked_at": {"x": center_x, "y": center_y},
        "occurrence": occurrence,
    }
    if "navigation_warning" in click_result:
        out["navigation_warning"] = click_result["navigation_warning"]

    # If center click produced no visual change, retry at expanding offsets.
    # Tries below first (most common: labels above inputs, buttons), then
    # above, right, left.  Each direction is tried at two distances.
    if not click_result.get("visual_change", True):
        small = max(60, match["height"] * 3)
        large = max(250, match["height"] * 15)
        offsets = [
            (0, small, "below"),
            (0, large, "below_far"),
            (0, -small, "above"),
            (0, -large, "above_far"),
            (small, 0, "right"),
            (-small, 0, "left"),
        ]
        for dx, dy, label in offsets:
            retry_x = center_x + dx
            retry_y = center_y + dy
            if retry_x < 0 or retry_y < 0:
                continue
            retry_result = do_click(retry_x, retry_y)
            if retry_result.get("visual_change", False):
                out["clicked_at"] = {"x": retry_x, "y": retry_y}
                out["retry"] = label
                if "navigation_warning" in retry_result:
                    out["navigation_warning"] = retry_result["navigation_warning"]
                elif "navigation_warning" in out:
                    del out["navigation_warning"]
                return out
        # All retries failed — return original click info
        out["retry"] = "none_worked"

    return out


def do_click_in_region(
    query: str,
    region: str = "center",
    case_sensitive: bool = False,
    window_title: Optional[str] = None,
) -> dict:
    """Find text within a screen region and click it.

    Regions: "top-left", "top", "top-right", "left", "center", "right",
             "bottom-left", "bottom", "bottom-right"

    Captures only the specified region for OCR, improving accuracy
    for small UI elements like compose windows.
    """
    from openowl.tools.screenshot import capture_screenshot, get_screen_size

    # Get screen dimensions
    monitors = get_screen_size()
    if len(monitors) < 2:
        return {"success": False, "error": "No monitor detected"}
    screen = monitors[1]  # Primary monitor
    sw, sh = screen["width"], screen["height"]

    # Define region bounds (thirds of the screen)
    regions = {
        "top-left":     {"x": 0,        "y": 0,        "w": sw//2,  "h": sh//2},
        "top":          {"x": sw//4,    "y": 0,        "w": sw//2,  "h": sh//2},
        "top-right":    {"x": sw//2,    "y": 0,        "w": sw//2,  "h": sh//2},
        "left":         {"x": 0,        "y": sh//4,    "w": sw//2,  "h": sh//2},
        "center":       {"x": sw//4,    "y": sh//4,    "w": sw//2,  "h": sh//2},
        "right":        {"x": sw//2,    "y": sh//4,    "w": sw//2,  "h": sh//2},
        "bottom-left":  {"x": 0,        "y": sh//2,    "w": sw//2,  "h": sh//2},
        "bottom":       {"x": sw//4,    "y": sh//2,    "w": sw//2,  "h": sh//2},
        "bottom-right": {"x": sw//2,    "y": sh//2,    "w": sw//2,  "h": sh//2},
    }

    region_name = region.lower().strip()
    if region_name not in regions:
        return {"success": False, "error": f"Unknown region '{region}'. Use: {', '.join(regions.keys())}"}

    rect = regions[region_name]

    # Capture just the region
    shot = capture_screenshot(region=rect)

    # Run OCR on the region screenshot (parallel normal + inverted)
    words, inv_words = _run_ocr_parallel(shot["path"])
    if inv_words:
        words = _deduplicate_words(words + inv_words)

    # Scale coordinates back to screen-absolute
    scale = rect["w"] / shot["width"] if shot["width"] < rect["w"] else 1.0
    for w in words:
        if scale != 1.0:
            w["x"] = round(w["x"] * scale)
            w["y"] = round(w["y"] * scale)
            w["width"] = round(w["width"] * scale)
            w["height"] = round(w["height"] * scale)
        w["x"] += rect["x"]
        w["y"] += rect["y"]

    # Search
    q = query if case_sensitive else query.lower()
    has_spaces = " " in q
    matches = _search_words(words, q, has_spaces, case_sensitive)

    if not matches:
        return {"success": False, "error": f"Text '{query}' not found in {region} region"}

    # Click the first match
    match = matches[0]
    center_x = match["x"] + match["width"] // 2
    center_y = match["y"] + match["height"] // 2

    click_result = do_click(center_x, center_y)

    return {
        "success": True,
        "match": match,
        "clicked_at": {"x": center_x, "y": center_y},
        "region": region_name,
        "visual_change": click_result.get("visual_change", False),
    }


# ------------------------------------------------------------------
# MCP tool registration
# ------------------------------------------------------------------

def register(server) -> int:
    """Register find_text, click_text, and click_in_region tools."""
    import base64 as _b64
    from mcp.server.fastmcp import Image as McpImage

    from openowl.tools.safety import with_timeout, ActionTimeoutError

    @server.tool()
    def find_text(
        query: str = "",
        text: str = "",
        case_sensitive: bool = False,
        window_title: str = "",
        near_y: int = 0,
    ) -> str:
        """Find text on screen using OCR. Use when accessibility targeting can't find an element.

        Takes a screenshot and runs OCR to find all occurrences of the query text.
        Returns locations with coordinates for each match.

        Parameters:
            query: Text to search for (substring match). (Alias: text)
            text: Alias for query — use either one.
            case_sensitive: Whether to match case (default False).
            window_title: Partial title of window to scope OCR to (default: full screen).
            near_y: When set (> 0), sort matches by proximity to this Y coordinate. Useful for disambiguating short text like "Add" that appears in multiple places.
        """
        search = query or text
        if not search:
            return "Error: provide either 'query' or 'text' parameter with the text to find."
        try:
            result = with_timeout(
                lambda: do_find_text(search, case_sensitive=case_sensitive,
                                     window_title=window_title or None,
                                     near_y=near_y if near_y > 0 else None),
                timeout=20.0,
            )
        except ActionTimeoutError:
            return f"Timed out after 20s running OCR for '{query}'."

        # Surface window scope warning if present
        warning = ""
        if result.get("window_scope_warning"):
            warning = f"\n  WARNING: {result['window_scope_warning']}"

        if not result["matches"]:
            return f"Text '{query}' not found. OCR detected {result['total_words']} words on screen.{warning}"

        lines = [f"Found {len(result['matches'])} match(es) for '{query}':"]
        for i, m in enumerate(result["matches"]):
            lines.append(
                f"[{i+1}] \"{m['text']}\" ({m['x']},{m['y']}) {m['width']}x{m['height']}"
            )
        if warning:
            lines.append(warning)
        return "\n".join(lines)

    @server.tool()
    def click_text(
        query: str = "",
        text: str = "",
        occurrence: int = 1,
        case_sensitive: bool = False,
        window_title: str = "",
        near_y: int = 0,
    ) -> list:
        """Find text on screen using OCR and click it. Fallback when accessibility can't find the element.

        Parameters:
            query: Text to search for and click. (Alias: text)
            text: Alias for query — use either one.
            occurrence: Which match to click if multiple (1 = first, default).
            case_sensitive: Whether to match case (default False).
            window_title: Partial title of window to scope OCR to (default: full screen).
            near_y: When set (> 0), prefer the match closest to this Y coordinate.
        """
        # Accept both 'query' and 'text' parameters
        search = query or text
        if not search:
            return "Error: provide either 'query' or 'text' parameter with the text to find."
        try:
            result = with_timeout(
                lambda: do_click_text(search, occurrence=occurrence,
                                      case_sensitive=case_sensitive,
                                      window_title=window_title or None,
                                      near_y=near_y if near_y > 0 else None),
                timeout=20.0,
            )
        except ActionTimeoutError:
            return f"Timed out after 20s running OCR click for '{query}'."

        if not result["success"]:
            return f"Failed: {result['error']}"

        m = result["match"]
        msg = f"Clicked text \"{m['text']}\" at ({result['clicked_at']['x']}, {result['clicked_at']['y']})."
        needs_screenshot = False
        if result.get("retry"):
            if result["retry"] == "none_worked":
                msg += "\nClicked the text but nothing changed on screen."
                needs_screenshot = True
            else:
                msg += f"\n↳ Center click had no effect — retried {result['retry']} and got a response."
        if result.get("navigation_warning"):
            msg += f"\n{result['navigation_warning']}"
            needs_screenshot = True

        # Only include screenshot on failure/navigation (saves vision tokens)
        if needs_screenshot:
            from openowl.tools.screenshot import capture_screenshot
            shot = capture_screenshot()
            return [
                McpImage(data=_b64.b64decode(shot["image"]), format="png"),
                msg,
            ]

        return msg

    @server.tool()
    def click_in_region(
        query: str,
        region: str = "center",
        case_sensitive: bool = False,
        window_title: str = "",
    ) -> list:
        """Find and click text within a screen region. More accurate than click_text for small UI areas.

        Narrows OCR to a portion of the screen, improving accuracy for
        compose windows, popups, dialogs, and sidebars.

        Parameters:
            query: Text to search for and click.
            region: Screen region — "top-left", "top", "top-right", "left", "center", "right", "bottom-left", "bottom", "bottom-right".
            case_sensitive: Whether to match case (default False).
            window_title: Partial title of window to scope to (default: full screen).
        """
        try:
            result = with_timeout(
                lambda: do_click_in_region(
                    query, region=region,
                    case_sensitive=case_sensitive,
                    window_title=window_title or None,
                ),
                timeout=20.0,
            )
        except ActionTimeoutError:
            return f"Timed out after 20s running regional OCR click for '{query}'."

        if not result["success"]:
            return f"Failed: {result['error']}"

        m = result["match"]
        msg = (
            f"Clicked \"{m['text']}\" at ({result['clicked_at']['x']}, {result['clicked_at']['y']}) "
            f"in {result['region']} region."
        )
        if result.get("visual_change"):
            msg += "\nScreen updated."
        else:
            msg += "\nNothing changed on screen."
            # Screenshot only when click didn't work
            from openowl.tools.screenshot import capture_screenshot
            shot = capture_screenshot()
            return [
                McpImage(data=_b64.b64decode(shot["image"]), format="png"),
                msg,
            ]

        return msg

    return 3

