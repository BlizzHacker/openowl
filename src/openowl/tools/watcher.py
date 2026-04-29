"""Notification watcher -- detect new windows/dialogs/toasts appearing.

Provides:
    start_watcher      - begin polling for new windows in a background thread
    stop_watcher       - stop the watcher and return event count
    get_notifications  - return accumulated events (new windows seen)

The watcher polls the window list periodically, diffs against a baseline,
and records any new windows as events.  Events are classified as 'toast',
'dialog', or 'window' based on size heuristics.
"""

import base64
import io
import threading
import time
from typing import Optional

from openowl.tools.windows import do_list_windows

# ------------------------------------------------------------------
# Window classification heuristics
# ------------------------------------------------------------------

_DIALOG_KEYWORDS = {
    "save", "open", "dialog", "alert", "confirm", "warning", "error",
    "permission", "allow", "deny", "update", "install", "setup",
    "properties", "preferences", "settings", "options", "about",
}


def classify_window_type(title: str, width: int, height: int) -> str:
    """Classify a window as 'toast', 'dialog', or 'window'.

    Parameters
    ----------
    title : str
        Window title.
    width, height : int
        Window dimensions.

    Returns
    -------
    str
        One of ``"toast"``, ``"dialog"``, ``"window"``.
    """
    # Toast: small notification-sized
    if width < 400 and height < 200:
        return "toast"

    # Dialog: medium or has dialog keywords in title
    title_lower = title.lower()
    has_keyword = any(kw in title_lower for kw in _DIALOG_KEYWORDS)
    if has_keyword or (width < 800 and height < 600):
        return "dialog"

    return "window"


# ------------------------------------------------------------------
# Watcher state
# ------------------------------------------------------------------

class _WatcherState:
    """Encapsulates all watcher state to avoid scattered globals."""

    def __init__(self):
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.events: list[dict] = []
        self.baseline_titles: set[str] = set()
        self.poll_interval: float = 1.0
        self.capture_snippets: bool = True

    def reset(self):
        self.running = False
        self.thread = None
        self.stop_event.clear()
        self.events.clear()
        self.baseline_titles.clear()


_state = _WatcherState()

# Cap on stored events to avoid unbounded memory growth
_MAX_EVENTS = 1000

# Minimum window size to report (filters out invisible/tiny system windows)
_MIN_WINDOW_SIZE = 50


# ------------------------------------------------------------------
# Background thread
# ------------------------------------------------------------------

def _watcher_loop():
    """Background polling loop.  Runs until stop_event is set."""
    while not _state.stop_event.is_set():
        try:
            current_windows = do_list_windows()
        except Exception:
            # Window enumeration failed — skip this cycle
            _state.stop_event.wait(_state.poll_interval)
            continue

        current_titles = set()
        window_map: dict[str, dict] = {}

        for w in current_windows:
            # Filter tiny/invisible windows
            if w.get("width", 0) < _MIN_WINDOW_SIZE or w.get("height", 0) < _MIN_WINDOW_SIZE:
                continue
            title = w.get("title", "")
            if title:
                current_titles.add(title)
                window_map[title] = w

        # Find new windows (titles not in baseline)
        new_titles = current_titles - _state.baseline_titles

        for title in new_titles:
            w = window_map[title]
            width = w.get("width", 0)
            height = w.get("height", 0)

            event = {
                "timestamp": time.time(),
                "type": classify_window_type(title, width, height),
                "title": title,
                "process_name": w.get("process_name", ""),
                "geometry": {
                    "x": w.get("x", 0),
                    "y": w.get("y", 0),
                    "width": width,
                    "height": height,
                },
                "snippet_b64": None,
            }

            # Optionally capture a region screenshot of the new window
            if _state.capture_snippets:
                try:
                    import mss
                    with mss.mss() as sct:
                        region = {
                            "left": max(0, w.get("x", 0)),
                            "top": max(0, w.get("y", 0)),
                            "width": max(1, width),
                            "height": max(1, height),
                        }
                        from PIL import Image
                        shot = sct.grab(region)
                        img = Image.frombytes("RGB", shot.size, shot.rgb)
                        # Downscale large snippets
                        if img.width > 640:
                            scale = 640 / img.width
                            img = img.resize(
                                (640, int(img.height * scale)),
                                Image.LANCZOS,
                            )
                        buf = io.BytesIO()
                        img.save(buf, format="JPEG", quality=70)
                        event["snippet_b64"] = base64.b64encode(
                            buf.getvalue()
                        ).decode("ascii")
                except Exception:
                    pass  # Snippet capture is best-effort

            with _state.lock:
                if len(_state.events) < _MAX_EVENTS:
                    _state.events.append(event)

        # Update baseline to include new windows (so they aren't re-reported)
        _state.baseline_titles = current_titles

        _state.stop_event.wait(_state.poll_interval)


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def do_start_watcher(poll_interval: float = 1.0, capture_snippets: bool = True) -> dict:
    """Start the background watcher thread.

    Returns
    -------
    dict
        ``{"started": True, "baseline_count": int}`` on success,
        ``{"error": str}`` if already running.
    """
    if _state.running:
        return {"error": "Watcher is already running. Stop it first."}

    _state.reset()
    _state.poll_interval = max(0.2, poll_interval)
    _state.capture_snippets = capture_snippets

    # Snapshot current windows as baseline
    try:
        windows = do_list_windows()
        for w in windows:
            title = w.get("title", "")
            if title and w.get("width", 0) >= _MIN_WINDOW_SIZE and w.get("height", 0) >= _MIN_WINDOW_SIZE:
                _state.baseline_titles.add(title)
    except Exception:
        pass

    _state.running = True
    _state.thread = threading.Thread(target=_watcher_loop, daemon=True, name="owl-watcher")
    _state.thread.start()

    return {"started": True, "baseline_count": len(_state.baseline_titles)}


def do_stop_watcher() -> dict:
    """Stop the background watcher thread.

    Returns
    -------
    dict
        ``{"stopped": True, "event_count": int}`` on success,
        ``{"error": str}`` if not running.
    """
    if not _state.running:
        return {"error": "Watcher is not running."}

    _state.stop_event.set()
    if _state.thread is not None:
        _state.thread.join(timeout=3.0)

    event_count = len(_state.events)
    _state.running = False
    _state.thread = None

    return {"stopped": True, "event_count": event_count}


def do_get_notifications(clear: bool = True) -> dict:
    """Return accumulated notification events.

    Parameters
    ----------
    clear : bool
        If True (default), clear the event queue after reading.

    Returns
    -------
    dict
        ``{"events": list[dict], "count": int, "watcher_running": bool}``
    """
    with _state.lock:
        events = list(_state.events)
        if clear:
            _state.events.clear()

    return {
        "events": events,
        "count": len(events),
        "watcher_running": _state.running,
    }


# ------------------------------------------------------------------
# MCP tool registration
# ------------------------------------------------------------------

def register(server) -> int:
    """Register *start_watcher*, *stop_watcher*, *get_notifications* tools.

    Returns the number of tools registered (3).
    """
    from mcp.server.fastmcp import Image as McpImage

    @server.tool()
    def start_watcher(
        poll_interval: float = 1.0,
        capture_snippets: bool = True,
    ) -> str:
        """Start watching for new windows, dialogs, and toasts.

        Runs in the background, polling every poll_interval seconds.
        New windows are recorded with their type (toast/dialog/window),
        title, geometry, and optionally a screenshot snippet.

        Use get_notifications to retrieve events, stop_watcher to end.

        Parameters:
            poll_interval: Seconds between polls (default 1.0, min 0.2).
            capture_snippets: Whether to screenshot new windows (default True).
        """
        result = do_start_watcher(
            poll_interval=poll_interval,
            capture_snippets=capture_snippets,
        )
        if "error" in result:
            return result["error"]
        return (
            f"Watcher started. Monitoring {result['baseline_count']} existing windows. "
            f"New windows will be recorded. Use get_notifications to check."
        )

    @server.tool()
    def stop_watcher() -> str:
        """Stop the notification watcher.

        Returns how many events were captured while running.
        """
        result = do_stop_watcher()
        if "error" in result:
            return result["error"]
        return f"Watcher stopped. {result['event_count']} event(s) captured."

    @server.tool()
    def get_notifications(clear: bool = True) -> list:
        """Get new windows/dialogs/toasts detected since the watcher started.

        Returns events with type, title, geometry, and optional screenshot
        snippet for each new window detected.

        Parameters:
            clear: Clear events after reading (default True).
        """
        result = do_get_notifications(clear=clear)

        if result["count"] == 0:
            status = "running" if result["watcher_running"] else "stopped"
            return [f"No new notifications. Watcher is {status}."]

        parts: list = []
        lines = [f"{result['count']} notification(s):"]

        for i, ev in enumerate(result["events"]):
            ts = time.strftime("%H:%M:%S", time.localtime(ev["timestamp"]))
            geo = ev["geometry"]
            lines.append(
                f"  [{i+1}] {ts} {ev['type'].upper()}: \"{ev['title']}\" "
                f"({geo['x']},{geo['y']} {geo['width']}x{geo['height']})"
            )
            if ev.get("process_name"):
                lines.append(f"       Process: {ev['process_name']}")

            # Include snippet images inline
            if ev.get("snippet_b64"):
                parts.append(
                    McpImage(
                        data=base64.b64decode(ev["snippet_b64"]),
                        format="jpeg",
                    )
                )

        parts.append("\n".join(lines))
        return parts

    return 3

