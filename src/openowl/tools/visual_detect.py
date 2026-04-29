"""Visual detection -- rectangle and text detection for accessibility-less apps.

When UIA returns insufficient elements, this module analyzes the screenshot
to detect clickable-looking regions (rectangles) and text labels, producing
a structured list that Claude can use for coordinate-aware clicking.

Uses OpenCV for contour detection and RapidOCR for text.
"""

import os
import sys
import tempfile

from PIL import Image

try:
    import cv2
except ImportError:
    cv2 = None
try:
    import numpy as np
except ImportError:
    np = None

_MIN_W = 20
_MIN_H = 15
_MAX_AREA_FRAC = 0.80


def detect_rectangles(img: Image.Image) -> list[dict]:
    """Detect rectangular UI elements in a PIL Image.

    Uses OpenCV when available (Windows). On macOS where cv2/numpy
    aren't installed, returns an empty list — OCR text detection
    is used instead via detect_text().

    Returns list of dicts: {"x", "y", "w", "h", "class"}
    where class is "button-like", "input-like", or "panel".
    Coordinates are in image-pixel space.
    """
    if cv2 is None or np is None:
        return []

    arr = np.array(img)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    edges = cv2.dilate(edges, kernel, iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    img_area = img.width * img.height
    rects = []

    for contour in contours:
        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
        if len(approx) < 4 or len(approx) > 6:
            continue
        x, y, w, h = cv2.boundingRect(approx)
        if w < _MIN_W or h < _MIN_H:
            continue
        if (w * h) > (img_area * _MAX_AREA_FRAC):
            continue
        aspect = w / h if h > 0 else 0
        if aspect >= 6.0 and h <= 50:
            cls = "input-like"
        elif aspect >= 2.0 and h <= 50:
            cls = "button-like"
        elif aspect >= 2.5:
            cls = "input-like"
        else:
            cls = "panel"
        rects.append({"x": x, "y": y, "w": w, "h": h, "class": cls})

    rects = _deduplicate_rects(rects)
    rects.sort(key=lambda r: r["w"] * r["h"], reverse=True)
    return rects


def _iou(a: dict, b: dict) -> float:
    """Intersection-over-union for two rectangles."""
    x1 = max(a["x"], b["x"])
    y1 = max(a["y"], b["y"])
    x2 = min(a["x"] + a["w"], b["x"] + b["w"])
    y2 = min(a["y"] + a["h"], b["y"] + b["h"])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    intersection = (x2 - x1) * (y2 - y1)
    union = a["w"] * a["h"] + b["w"] * b["h"] - intersection
    return intersection / union if union > 0 else 0.0


def _deduplicate_rects(rects: list[dict], iou_threshold: float = 0.7) -> list[dict]:
    """Remove overlapping rectangles, keeping the tighter (smaller) one."""
    if not rects:
        return []
    sorted_rects = sorted(rects, key=lambda r: r["w"] * r["h"])
    keep = []
    for rect in sorted_rects:
        is_dup = False
        for kept in keep:
            if _iou(rect, kept) > iou_threshold:
                is_dup = True
                break
        if not is_dup:
            keep.append(rect)
    return keep


# ---------------------------------------------------------------------------
# OCR text detection
# ---------------------------------------------------------------------------


def _run_ocr_on_image(img: Image.Image) -> list[dict]:
    """Run OCR on a PIL Image. Uses the platform's best engine.

    Returns empty list on any OCR failure (best-effort fallback).
    """
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        img.save(f, format="PNG")
        tmp_path = f.name
    try:
        from openowl.tools.ocr import _run_ocr_engine
        return _run_ocr_engine(tmp_path)
    except Exception as exc:
        print(f"[visual_detect] OCR failed: {exc}", file=sys.stderr)
        return []
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def detect_text(img: Image.Image) -> list[dict]:
    """Detect text regions via OCR. Returns list of {"type": "text", "x", "y", "w", "h", "text"}."""
    words = _run_ocr_on_image(img)
    return [
        {"type": "text", "x": w["x"], "y": w["y"], "w": w["width"], "h": w["height"], "text": w["text"]}
        for w in words
    ]


_MAX_REGIONS = 30


def _text_inside_rect(text: dict, rect: dict, padding: int = 10) -> bool:
    """Check if a text region is inside (or nearly inside) a rectangle."""
    return (
        text["x"] >= rect["x"] - padding
        and text["y"] >= rect["y"] - padding
        and text["x"] + text["w"] <= rect["x"] + rect["w"] + padding
        and text["y"] + text["h"] <= rect["y"] + rect["h"] + padding
    )


def merge_regions(rects: list[dict], texts: list[dict]) -> list[dict]:
    """Merge detected rectangles and text regions.

    Text inside a rect: rect gets "text" field. Standalone text kept separately.
    Capped at _MAX_REGIONS, sorted by area descending.
    """
    claimed = set()
    result_rects = []
    for rect in rects:
        r = dict(rect)  # shallow copy — avoid mutating caller's data
        labels = []
        for i, text in enumerate(texts):
            if _text_inside_rect(text, r):
                labels.append(text["text"])
                claimed.add(i)
        if labels:
            r["text"] = " ".join(labels)
        r["type"] = "rect"
        result_rects.append(r)
    standalone = [texts[i] for i in range(len(texts)) if i not in claimed]
    result_rects.sort(key=lambda r: r["w"] * r["h"], reverse=True)
    return (result_rects + standalone)[:_MAX_REGIONS]


# ---------------------------------------------------------------------------
# Main entry point and text formatting
# ---------------------------------------------------------------------------


def detect_ui_regions(img: Image.Image, scale: float = 1.0) -> list[dict]:
    """Detect UI regions in a screenshot image.

    Runs rectangle detection + OCR, merges, and scales to screen-space.

    Parameters:
        img: The (possibly downscaled) screenshot PIL Image.
        scale: Downscale ratio (original_width / image_width). Coordinates
               are multiplied by this to produce screen-space values.
    """
    rects = detect_rectangles(img)
    texts = detect_text(img)
    merged = merge_regions(rects, texts)
    if scale != 1.0:
        for region in merged:
            region["x"] = int(region["x"] * scale)
            region["y"] = int(region["y"] * scale)
            region["w"] = int(region["w"] * scale)
            region["h"] = int(region["h"] * scale)
    return merged


_CLASS_SHORT = {
    "button-like": "btn",
    "input-like": "input",
}


def format_regions_text(regions: list[dict]) -> str:
    """Format detected regions as a compact text list for the screenshot response."""
    if not regions:
        return ""
    lines = ["", "Detected UI regions:"]
    for i, r in enumerate(regions):
        label = f'"{r["text"]}" ' if r.get("text") else ""
        cls = _CLASS_SHORT.get(r.get("class", ""), r.get("class", ""))
        rtype = r.get("type", "rect")
        if label:
            line = f'[{i+1}] {label}{cls} ({r["x"]},{r["y"]}) {r["w"]}x{r["h"]}'
        elif rtype == "text":
            line = f'[{i+1}] text "{r.get("text", "")}" ({r["x"]},{r["y"]}) {r["w"]}x{r["h"]}'
        else:
            line = f'[{i+1}] ({r["x"]},{r["y"]}) {r["w"]}x{r["h"]} {cls}'
        lines.append(line)
    return "\n".join(lines)

