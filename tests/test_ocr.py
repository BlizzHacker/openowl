"""Tests for OCR content-area scoring in tools/ocr.py."""

from openowl.tools.ocr import _score_content_area


class TestContentAreaScoring:
    """Tests for _score_content_area()."""

    WINDOW = {"x": 0, "y": 0, "w": 1280, "h": 800}

    def _match(self, x: int, y: int) -> dict:
        return {"x": x, "y": y, "width": 60, "height": 16}

    def test_chrome_deprioritised(self):
        """Match in top 150px (browser chrome) gets +200 penalty."""
        chrome = _score_content_area(self._match(400, 50), self.WINDOW)
        content = _score_content_area(self._match(400, 400), self.WINDOW)
        assert chrome > content
        assert chrome >= 200  # chrome penalty applied

    def test_sidebar_deprioritised(self):
        """Match in left/right 15% gets +100 penalty."""
        sidebar_left = _score_content_area(self._match(50, 400), self.WINDOW)
        sidebar_right = _score_content_area(self._match(1200, 400), self.WINDOW)
        content = _score_content_area(self._match(640, 400), self.WINDOW)
        assert sidebar_left > content
        assert sidebar_right > content
        assert sidebar_left >= 100
        assert sidebar_right >= 100

    def test_y_tiebreaker(self):
        """Among content-area matches, higher Y (further down) scores higher."""
        top = _score_content_area(self._match(640, 200), self.WINDOW)
        bottom = _score_content_area(self._match(640, 500), self.WINDOW)
        # Both in content area (no penalties), bottom has higher y tiebreaker
        assert bottom > top
        # But difference should be small (0-10 range)
        assert abs(bottom - top) <= 10

    def test_none_window_rect_no_crash(self):
        """With no window context, score is still computed (y-based fallback)."""
        score = _score_content_area(self._match(400, 300), None)
        assert isinstance(score, float)
        assert score >= 0

    def test_prefer_content_reorders(self):
        """When prefer_content=True, content match comes before chrome match."""
        from unittest.mock import patch

        window_rect = {"x": 0, "y": 0, "w": 1280, "h": 800}

        # Mock OCR, window rect lookup, and screenshot capture
        with patch("openowl.tools.ocr._run_ocr") as mock_ocr, \
             patch("openowl.tools.ocr._get_window_rect", return_value=window_rect), \
             patch("openowl.tools.screenshot.capture_screenshot", return_value={"path": "/fake.png", "image": "", "width": 1280, "height": 800}), \
             patch("openowl.tools.target_window.get_target", return_value="MyApp"):

            mock_ocr.return_value = [
                # Chrome area (y=50, within top 150px -> +200 penalty)
                {"text": "Issues", "x": 400, "y": 50, "width": 60, "height": 16},
                # Content area (y=400, no penalties)
                {"text": "Issues", "x": 400, "y": 400, "width": 60, "height": 16},
                {"text": "other", "x": 100, "y": 100, "width": 40, "height": 16},
            ]

            from openowl.tools.ocr import do_find_text
            result = do_find_text("Issues", prefer_content=True, window_title="MyApp")
            matches = result["matches"]
            assert len(matches) == 2
            # Content match (y=400, low score) before chrome match (y=50, +200 penalty)
            assert matches[0]["y"] == 400
            assert matches[1]["y"] == 50

    def test_prefer_content_false_preserves_y_x(self):
        """When prefer_content=False, matches stay in (y, x) order."""
        from unittest.mock import patch

        with patch("openowl.tools.ocr._run_ocr") as mock_ocr, \
             patch("openowl.tools.ocr._capture_image_path", return_value="/fake.png"), \
             patch("openowl.tools.target_window.get_target", return_value=None):
            mock_ocr.return_value = [
                {"text": "Issues", "x": 400, "y": 50, "width": 60, "height": 16},
                {"text": "Issues", "x": 400, "y": 400, "width": 60, "height": 16},
            ]

            from openowl.tools.ocr import do_find_text
            result = do_find_text("Issues", prefer_content=False, window_title=None)
            matches = result["matches"]
            assert len(matches) == 2
            # Standard (y, x) order: y=50 first
            assert matches[0]["y"] == 50
            assert matches[1]["y"] == 400

