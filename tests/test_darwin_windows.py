"""Tests for macOS window management functions in owl_platform.darwin_backend.

All tests are skipped on non-darwin platforms.
"""

import sys
import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="macOS-only tests"
)


class TestDarwinListWindows:
    """Tests for list_windows_native()."""

    def test_returns_list(self):
        from openowl.owl_platform import list_windows_native

        result = list_windows_native()
        assert isinstance(result, list)

    def test_windows_have_required_keys(self):
        from openowl.owl_platform import list_windows_native

        result = list_windows_native()
        required_keys = {"title", "process_name", "x", "y", "width", "height"}
        for window in result:
            assert required_keys.issubset(window.keys()), (
                f"Window missing keys: {required_keys - window.keys()}"
            )

    def test_windows_have_process_name(self):
        from openowl.owl_platform import list_windows_native

        result = list_windows_native()
        for window in result:
            # Every window should have a non-empty process_name
            assert isinstance(window["process_name"], str)
            assert len(window["process_name"]) > 0


class TestDarwinGetForegroundTitle:
    """Tests for get_foreground_title()."""

    def test_returns_string(self):
        from openowl.owl_platform import get_foreground_title

        result = get_foreground_title()
        assert isinstance(result, str)


class TestDarwinFocusWindow:
    """Tests for focus_window_native()."""

    def test_nonexistent_window_returns_failure(self):
        from openowl.owl_platform import focus_window_native

        result = focus_window_native("ZZZZZ_NONEXISTENT_WINDOW_12345", "focus")
        assert isinstance(result, dict)
        assert result["success"] is False
        assert "error" in result

