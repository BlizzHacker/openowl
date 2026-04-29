"""Tests for scroll verification and click visual change detection in tools/input_tools.py."""

import base64
import io

import numpy as np
from PIL import Image
from unittest.mock import patch, MagicMock


def _make_b64(arr: np.ndarray) -> str:
    """Encode a numpy array as a base64 JPEG string."""
    img = Image.fromarray(arr.astype(np.uint8))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _make_screenshot_result(arr: np.ndarray) -> dict:
    """Build a fake capture_screenshot() result dict."""
    b64 = _make_b64(arr)
    return {
        "image": b64,
        "width": arr.shape[1],
        "height": arr.shape[0],
        "path": "/fake.png",
        "dpi_scale": 1.0,
    }


class TestScrollVerification:
    """Tests for do_scroll() before/after screenshot comparison."""

    def test_scroll_detected_when_diff_high(self):
        """When screen content changes after scroll, scroll_detected=True."""
        before = _make_screenshot_result(np.full((100, 100, 3), 128, dtype=np.uint8))
        after = _make_screenshot_result(np.full((100, 100, 3), 200, dtype=np.uint8))

        with patch("openowl.tools.input_tools.pyautogui"), \
             patch("openowl.tools.target_window.ensure_focus"), \
             patch("openowl.tools.screenshot.capture_screenshot", side_effect=[before, after]):
            from openowl.tools.input_tools import do_scroll
            result = do_scroll(640, 400, "down", amount=3)

        assert result["scroll_detected"] is True
        assert result["pixel_diff"] > 0.005
        assert "scroll_warning" not in result

    def test_scroll_warning_when_diff_low(self):
        """When screen doesn't change, scroll_detected=False with warning."""
        same = _make_screenshot_result(np.full((100, 100, 3), 128, dtype=np.uint8))

        with patch("openowl.tools.input_tools.pyautogui"), \
             patch("openowl.tools.target_window.ensure_focus"), \
             patch("openowl.tools.screenshot.capture_screenshot", return_value=same):
            from openowl.tools.input_tools import do_scroll
            result = do_scroll(640, 400, "down", amount=3)

        assert result["scroll_detected"] is False
        assert result["pixel_diff"] < 0.005
        assert "scroll_warning" in result


class TestClickVisualChange:
    """Tests for do_click() before/after screenshot comparison."""

    def test_visual_change_detected(self):
        """When screen changes around click point, visual_change=True."""
        before = _make_screenshot_result(np.full((400, 400, 3), 128, dtype=np.uint8))
        after = _make_screenshot_result(np.full((400, 400, 3), 200, dtype=np.uint8))

        with patch("openowl.tools.input_tools.pyautogui"), \
             patch("openowl.tools.target_window.ensure_focus"), \
             patch("openowl.tools.screenshot.capture_screenshot", side_effect=[before, after]), \
             patch("openowl.tools.windows.get_foreground_title", return_value="MyApp"):
            from openowl.tools.input_tools import do_click
            result = do_click(640, 400)

        assert result["visual_change"] is True
        assert result["pixel_diff"] > 0.005

    def test_no_visual_change(self):
        """When screen doesn't change around click point, visual_change=False."""
        same = _make_screenshot_result(np.full((400, 400, 3), 128, dtype=np.uint8))

        with patch("openowl.tools.input_tools.pyautogui"), \
             patch("openowl.tools.target_window.ensure_focus"), \
             patch("openowl.tools.screenshot.capture_screenshot", return_value=same), \
             patch("openowl.tools.windows.get_foreground_title", return_value="MyApp"):
            from openowl.tools.input_tools import do_click
            result = do_click(640, 400)

        assert result["visual_change"] is False
        assert result["pixel_diff"] < 0.005

    def test_visual_change_with_navigation_warning(self):
        """visual_change and navigation_warning can coexist."""
        before = _make_screenshot_result(np.full((400, 400, 3), 128, dtype=np.uint8))
        after = _make_screenshot_result(np.full((400, 400, 3), 200, dtype=np.uint8))

        with patch("openowl.tools.input_tools.pyautogui"), \
             patch("openowl.tools.target_window.ensure_focus"), \
             patch("openowl.tools.screenshot.capture_screenshot", side_effect=[before, after]), \
             patch("openowl.tools.windows.get_foreground_title", side_effect=["Page A", "Page B"]):
            from openowl.tools.input_tools import do_click
            result = do_click(640, 400)

        assert result["visual_change"] is True
        assert "navigation_warning" in result

