"""Manages screenshot file lifecycle with a rolling cap."""

import os
import time
import threading
from typing import Dict, List


class ScreenshotManager:
    """Saves, lists, and cleans up screenshot files in a directory.

    Enforces a configurable maximum number of screenshots, automatically
    purging the oldest files when the cap is exceeded.
    """

    def __init__(self, directory: str, max_screenshots: int = 50):
        if max_screenshots < 1:
            raise ValueError("max_screenshots must be at least 1")
        self._directory = directory
        self._max_screenshots = max_screenshots
        self._seq = 0
        self._lock = threading.Lock()
        os.makedirs(directory, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(self, image_data: bytes) -> str:
        """Save image bytes to disk and enforce the rolling cap.

        File naming: ``owl_{timestamp_ms}_{seq}.png``

        Returns the absolute path to the saved file.
        """
        with self._lock:
            timestamp_ms = int(time.time() * 1000)
            self._seq += 1
            filename = f"owl_{timestamp_ms}_{self._seq}.png"
            path = os.path.join(self._directory, filename)

            with open(path, "wb") as f:
                f.write(image_data)

            self._enforce_cap()
            return path

    def list_screenshots(self) -> List[Dict]:
        """Return metadata for every screenshot, sorted newest-first.

        Each entry is a dict with keys: ``path``, ``name``, ``created``, ``size``.
        """
        entries: List[Dict] = []
        for name in os.listdir(self._directory):
            if not name.endswith(".png"):
                continue
            full = os.path.join(self._directory, name)
            if not os.path.isfile(full):
                continue
            stat = os.stat(full)
            entries.append({
                "path": full,
                "name": name,
                "created": stat.st_ctime,
                "size": stat.st_size,
            })
        entries.sort(key=lambda e: e["created"], reverse=True)
        return entries

    def cleanup(self) -> int:
        """Remove **all** screenshots and return the count removed."""
        screenshots = self.list_screenshots()
        removed = 0
        for entry in screenshots:
            try:
                os.remove(entry["path"])
                removed += 1
            except OSError:
                pass
        return removed

    def set_limit(self, new_limit: int) -> None:
        """Change the maximum number of screenshots and immediately enforce."""
        if new_limit < 1:
            raise ValueError("new_limit must be at least 1")
        with self._lock:
            self._max_screenshots = new_limit
            self._enforce_cap()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _enforce_cap(self) -> None:
        """Delete oldest screenshots until count <= max."""
        screenshots = self.list_screenshots()
        while len(screenshots) > self._max_screenshots:
            oldest = screenshots.pop()  # last item = oldest (sorted newest-first)
            try:
                os.remove(oldest["path"])
            except OSError:
                pass

