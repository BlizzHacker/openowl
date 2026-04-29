"""Tests for compare_screenshots() in tools/screenshot.py."""

import base64
import io

import numpy as np
from PIL import Image

from openowl.tools.screenshot import compare_screenshots


def _make_b64(arr: np.ndarray) -> str:
    """Encode a numpy array as a base64 JPEG string."""
    img = Image.fromarray(arr.astype(np.uint8))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return base64.b64encode(buf.getvalue()).decode("ascii")


class TestCompareScreenshots:
    def test_identical_returns_zero(self):
        arr = np.full((100, 100, 3), 128, dtype=np.uint8)
        b64 = _make_b64(arr)
        assert compare_screenshots(b64, b64) == 0.0

    def test_black_vs_white_returns_high(self):
        black = _make_b64(np.zeros((100, 100, 3), dtype=np.uint8))
        white = _make_b64(np.full((100, 100, 3), 255, dtype=np.uint8))
        diff = compare_screenshots(black, white)
        # JPEG compression means not exactly 1.0, but should be very high
        assert diff > 0.9

    def test_size_mismatch_returns_one(self):
        small = _make_b64(np.zeros((50, 50, 3), dtype=np.uint8))
        large = _make_b64(np.zeros((100, 100, 3), dtype=np.uint8))
        assert compare_screenshots(small, large) == 1.0

    def test_small_change_returns_small_value(self):
        arr = np.full((100, 100, 3), 128, dtype=np.uint8)
        before = _make_b64(arr)
        # Change a 10x10 patch (1% of pixels)
        arr[0:10, 0:10] = 255
        after = _make_b64(arr)
        diff = compare_screenshots(before, after)
        assert 0.0 < diff < 0.2

