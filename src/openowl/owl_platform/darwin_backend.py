"""macOS (Darwin) platform backend for openowl.

Implements platform-specific functions for macOS. Functions not yet ported
raise NotImplementedError with a reference to the task that will implement them.
"""
from __future__ import annotations

import os

__all__ = [
    "get_dpi_scale",
    "get_foreground_title",
    "list_windows_native",
    "focus_window_native",
    "classify_window_native",
    "get_process_name_for_pid",
    "is_elevated",
    "run_ocr_native",
    "send_text_to_console",
    "send_keys_to_console",
    "get_foreground_hwnd",
    "get_class_name",
    "get_loaded_modules",
    "find_host_terminal_hwnd",
    # AXUIElement accessibility (Task 6)
    "windows_role_to_ax",
    "ax_role_to_windows",
    "ax_element_to_dict",
    "ax_get_frontmost_app",
    "ax_get_app_for_title",
    "ax_find_elements",
    "ax_get_focused_element",
]


# ---------------------------------------------------------------------------
# Implemented
# ---------------------------------------------------------------------------


def get_dpi_scale() -> float:
    """Return the Retina backing-scale factor (e.g. 2.0 on Retina, 1.0 otherwise)."""
    try:
        from AppKit import NSScreen  # type: ignore[import-untyped]

        screen = NSScreen.mainScreen()
        if screen is not None:
            return float(screen.backingScaleFactor())
        return 1.0
    except ImportError:
        return 1.0


def is_elevated(pid: int = None) -> bool:
    """Return True if running as root (euid == 0)."""
    return os.geteuid() == 0


# ---------------------------------------------------------------------------
# Console functions -- no-ops on macOS (input goes through pyautogui)
# ---------------------------------------------------------------------------


def send_text_to_console(pid, text, hwnd=0) -> dict:
    """No-op on macOS; input is handled via pyautogui passthrough."""
    return {"success": True, "method": "pyautogui_passthrough"}


def send_keys_to_console(pid, keys, hwnd=0) -> dict:
    """No-op on macOS; input is handled via pyautogui passthrough."""
    return {"success": True, "method": "pyautogui_passthrough"}


# ---------------------------------------------------------------------------
# Not applicable on macOS (Windows-only concepts)
# ---------------------------------------------------------------------------


def get_foreground_hwnd() -> int:
    """HWND is a Windows concept; return 0 on macOS."""
    return 0


def get_class_name(hwnd) -> str:
    """Window class name is a Windows concept; return empty string on macOS."""
    return ""


# ---------------------------------------------------------------------------
# Window management (Task 4)
# ---------------------------------------------------------------------------


def _cg_window_list() -> list[dict]:
    """Return the raw CGWindowListCopyWindowInfo result as a Python list.

    Returns an empty list if Quartz is not available.
    """
    try:
        from Quartz import (  # type: ignore[import-untyped]
            CGWindowListCopyWindowInfo,
            kCGWindowListOptionOnScreenOnly,
            kCGNullWindowID,
        )
    except ImportError:
        return []

    info = CGWindowListCopyWindowInfo(
        kCGWindowListOptionOnScreenOnly, kCGNullWindowID
    )
    return list(info) if info else []


def list_windows_native() -> list[dict]:
    """List visible on-screen windows using CGWindowListCopyWindowInfo.

    Filters to layer 0 (normal windows) and skips windows without a title
    *and* without an owner name.

    Returns a list of dicts with keys:
        title, process_name, pid, x, y, width, height
    """
    try:
        from Quartz import (  # type: ignore[import-untyped]
            kCGWindowName,
            kCGWindowOwnerName,
            kCGWindowOwnerPID,
            kCGWindowBounds,
            kCGWindowLayer,
        )
    except ImportError:
        return []

    results: list[dict] = []
    for w in _cg_window_list():
        # Only include normal windows (layer 0)
        if w.get(kCGWindowLayer, -1) != 0:
            continue

        title = w.get(kCGWindowName, "") or ""
        owner = w.get(kCGWindowOwnerName, "") or ""

        # Skip windows with neither a title nor an owner name
        if not title and not owner:
            continue

        bounds = w.get(kCGWindowBounds, {})
        results.append({
            "title": title if title else owner,
            "process_name": owner,
            "pid": w.get(kCGWindowOwnerPID),
            "x": int(bounds.get("X", 0)),
            "y": int(bounds.get("Y", 0)),
            "width": int(bounds.get("Width", 0)),
            "height": int(bounds.get("Height", 0)),
        })

    return results


def get_foreground_title() -> str:
    """Return the title of the frontmost window on macOS.

    Uses NSWorkspace.frontmostApplication() to find the active app, then
    searches CGWindowListCopyWindowInfo for the first matching window by PID.
    Falls back to the app's localizedName if no window title is found.
    """
    try:
        from AppKit import NSWorkspace  # type: ignore[import-untyped]
        from Quartz import (  # type: ignore[import-untyped]
            kCGWindowName,
            kCGWindowOwnerPID,
            kCGWindowLayer,
        )
    except ImportError:
        return ""

    ws = NSWorkspace.sharedWorkspace()
    app = ws.frontmostApplication()
    if app is None:
        return ""

    pid = app.processIdentifier()
    app_name = app.localizedName() or ""

    # Search for the first layer-0 window belonging to this app
    for w in _cg_window_list():
        if w.get(kCGWindowOwnerPID) != pid:
            continue
        if w.get(kCGWindowLayer, -1) != 0:
            continue
        name = w.get(kCGWindowName, "") or ""
        if name:
            return name

    # Fallback to the application name
    return app_name


def focus_window_native(title: str, action: str) -> dict:
    """Focus, minimize, maximize, or restore a window on macOS.

    Parameters
    ----------
    title : str
        Partial window title or process name to match (case-insensitive).
    action : str
        One of ``focus``, ``minimize``, ``maximize``, ``restore``.

    Returns
    -------
    dict
        ``{"success": True, "window": str, "action": str}`` on success,
        ``{"success": False, "error": str}`` on failure.
    """
    try:
        from Quartz import (  # type: ignore[import-untyped]
            kCGWindowName,
            kCGWindowOwnerName,
            kCGWindowOwnerPID,
            kCGWindowLayer,
        )
        from AppKit import (  # type: ignore[import-untyped]
            NSRunningApplication,
            NSApplicationActivateIgnoringOtherApps,
        )
    except ImportError:
        return {"success": False, "error": "PyObjC (Quartz/AppKit) not available"}

    title_lower = title.lower()

    # Search for matching window
    matched_pid: int | None = None
    matched_title: str = ""

    for w in _cg_window_list():
        if w.get(kCGWindowLayer, -1) != 0:
            continue

        win_name = w.get(kCGWindowName, "") or ""
        owner_name = w.get(kCGWindowOwnerName, "") or ""

        if title_lower in win_name.lower() or title_lower in owner_name.lower():
            matched_pid = w.get(kCGWindowOwnerPID)
            matched_title = win_name if win_name else owner_name
            break

    if matched_pid is None:
        return {"success": False, "error": f"No window matching '{title}' found"}

    # -- action: focus -------------------------------------------------------
    if action == "focus":
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(
            matched_pid
        )
        if app is None:
            return {"success": False, "error": f"No running app for PID {matched_pid}"}

        # Use osascript to activate — more reliable than activateWithOptions_
        # when called from a child process (e.g. MCP server under Terminal).
        # activateWithOptions_ often loses focus back to the parent Terminal.
        import subprocess as _sp
        app_name = owner_name if owner_name else matched_title
        try:
            _sp.run(
                ["osascript", "-e",
                 f'tell application "{app_name}" to activate'],
                capture_output=True, timeout=2,
            )
        except Exception:
            # Fallback to activateWithOptions_ if osascript fails
            app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)

        # Poll until the app is actually frontmost
        import time
        for _ in range(50):  # up to ~500ms
            time.sleep(0.01)
            if app.isActive():
                break
        return {"success": True, "window": matched_title, "action": action}

    # -- actions requiring AXUIElement: minimize / maximize / restore ---------
    try:
        from ApplicationServices import (  # type: ignore[import-untyped]
            AXUIElementCreateApplication,
            AXUIElementSetAttributeValue,
            AXUIElementCopyAttributeValue,
            AXUIElementPerformAction,
        )
    except ImportError:
        return {
            "success": False,
            "error": "pyobjc-framework-ApplicationServices not installed",
        }

    ax_app = AXUIElementCreateApplication(matched_pid)

    # Get the list of AXWindows
    err, ax_windows = AXUIElementCopyAttributeValue(ax_app, "AXWindows", None)
    if err != 0 or not ax_windows:
        # Accessibility API may not be permitted
        if action == "focus":
            # Already handled above, but just in case
            pass
        return {
            "success": False,
            "error": (
                f"Cannot access AXWindows for PID {matched_pid} "
                f"(error {err}). Accessibility permission may be required."
            ),
        }

    # Find the matching AX window by title
    target_ax_win = None
    for ax_win in ax_windows:
        err2, ax_title = AXUIElementCopyAttributeValue(ax_win, "AXTitle", None)
        ax_title_str = (ax_title or "") if err2 == 0 else ""
        if title_lower in ax_title_str.lower() or ax_title_str == matched_title:
            target_ax_win = ax_win
            break

    # If no title match found, fall back to the first window
    if target_ax_win is None and ax_windows:
        target_ax_win = ax_windows[0]

    if target_ax_win is None:
        return {"success": False, "error": f"No AX window found for '{title}'"}

    if action == "minimize":
        AXUIElementSetAttributeValue(target_ax_win, "AXMinimized", True)
    elif action == "maximize":
        # AXZoomWindow is the equivalent of clicking the green zoom button
        AXUIElementPerformAction(target_ax_win, "AXZoomWindow")
    elif action == "restore":
        AXUIElementSetAttributeValue(target_ax_win, "AXMinimized", False)
        # Also bring app to front
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(
            matched_pid
        )
        if app is not None:
            app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
    else:
        return {"success": False, "error": f"Unknown action: {action}"}

    return {"success": True, "window": matched_title, "action": action}


def get_process_name_for_pid(pid: int) -> str:
    """Return the process (application) name for a given PID on macOS."""
    try:
        from AppKit import NSRunningApplication  # type: ignore[import-untyped]
    except ImportError:
        return ""
    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if app is None:
        return ""
    return app.localizedName() or ""


def find_host_terminal_hwnd() -> int | None:
    """Return the CGWindowID of the terminal that launched this process.

    On macOS there is no HWND concept; we return the CGWindow number of
    the first window belonging to our parent process's application, or
    None if it cannot be determined.
    """
    try:
        from Quartz import (  # type: ignore[import-untyped]
            kCGWindowOwnerPID,
            kCGWindowLayer,
            kCGWindowNumber,
        )
    except ImportError:
        return None

    ppid = os.getppid()
    for w in _cg_window_list():
        if w.get(kCGWindowOwnerPID) == ppid and w.get(kCGWindowLayer, -1) == 0:
            return w.get(kCGWindowNumber)
    return None


# ---------------------------------------------------------------------------
# Window classification (Task 5)
# ---------------------------------------------------------------------------

# macOS bundle-based window classification
_TERMINAL_BUNDLES = {
    "com.apple.Terminal", "com.googlecode.iterm2", "io.alacritty",
    "com.mitchellh.ghostty", "co.zeit.hyper", "dev.warp.Warp-Stable",
    "net.kovidgoyal.kitty",
}

_BROWSER_BUNDLES = {
    "com.google.Chrome", "com.apple.Safari", "org.mozilla.firefox",
    "com.brave.Browser", "com.operasoftware.Opera", "com.vivaldi.Vivaldi",
    "company.thebrowser.Browser",  # Arc
}

_ELECTRON_BUNDLES = {
    "com.microsoft.VSCode", "com.tinyspeck.slackmacgap",
    "com.hnc.Discord", "md.obsidian", "notion.id",
    "com.postmanlabs.mac", "com.github.GitHubClient",
}


def _classify_by_bundle(bundle_id: str, process_name: str) -> str:
    """Classify a macOS app by bundle identifier and process name."""
    if bundle_id in _TERMINAL_BUNDLES:
        return "terminal"
    if bundle_id in _BROWSER_BUNDLES:
        return "browser"
    if bundle_id in _ELECTRON_BUNDLES:
        return "electron"
    if "electron" in process_name.lower():
        return "electron"
    return "generic"


def classify_window_native(handle=None) -> dict:
    """Classify the frontmost (or given) app's window type on macOS.

    Parameters
    ----------
    handle : optional
        Ignored on macOS (kept for API compatibility with win32_backend).
    """
    try:
        from AppKit import NSWorkspace, NSRunningApplication  # type: ignore[import-untyped]
    except ImportError:
        return {"type": "generic", "process_name": "", "bundle_id": "", "pid": 0}

    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return {"type": "generic", "process_name": "", "bundle_id": "", "pid": 0}

    bundle_id = app.bundleIdentifier() or ""
    process_name = app.localizedName() or ""
    app_pid = app.processIdentifier()

    win_type = _classify_by_bundle(bundle_id, process_name)

    return {
        "type": win_type,
        "process_name": process_name,
        "bundle_id": bundle_id,
        "pid": app_pid,
        "is_elevated": os.geteuid() == 0,
    }


# ---------------------------------------------------------------------------
# Framework detection helpers (Task 8)
# ---------------------------------------------------------------------------


def _get_app_info_for_title(title: str | None = None) -> dict | None:
    """Get app info (bundle_id, process_name, pid) for a window title.

    If title is None, uses the frontmost application.
    Returns None if no match found.
    """
    try:
        from AppKit import NSWorkspace, NSRunningApplication  # type: ignore[import-untyped]
        from Quartz import (  # type: ignore[import-untyped]
            kCGWindowName,
            kCGWindowOwnerName,
            kCGWindowOwnerPID,
            kCGWindowLayer,
        )
    except ImportError:
        return None

    if title is None:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return None
        return {
            "bundle_id": app.bundleIdentifier() or "",
            "process_name": app.localizedName() or "",
            "pid": app.processIdentifier(),
        }

    title_lower = title.lower()
    for w in _cg_window_list():
        if w.get(kCGWindowLayer, -1) != 0:
            continue
        win_name = w.get(kCGWindowName, "") or ""
        owner_name = w.get(kCGWindowOwnerName, "") or ""
        if title_lower in win_name.lower() or title_lower in owner_name.lower():
            pid = w.get(kCGWindowOwnerPID)
            if pid:
                app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
                if app:
                    return {
                        "bundle_id": app.bundleIdentifier() or "",
                        "process_name": app.localizedName() or "",
                        "pid": pid,
                    }
    return None


# macOS framework detection by bundle ID patterns
_COCOA_BUNDLES = {
    "com.apple.Safari", "com.apple.finder", "com.apple.Preview",
    "com.apple.TextEdit", "com.apple.mail", "com.apple.iCal",
    "com.apple.AddressBook", "com.apple.Notes", "com.apple.reminders",
    "com.apple.Maps", "com.apple.Photos", "com.apple.dt.Xcode",
}

_QT_BUNDLES = {
    "org.qt-project.",  # prefix match
}

_JAVA_BUNDLES = {
    "com.jetbrains.",  # prefix match -- IntelliJ, PyCharm, etc.
}

# Framework -> (accessibility_support, hints) for macOS
_DARWIN_FRAMEWORK_INFO: dict[str, tuple[str, list[str]]] = {
    "cocoa": (
        "full",
        [
            "Native Cocoa app detected. Full AXUIElement accessibility support.",
            "Use AXUIElement APIs for reliable element identification.",
        ],
    ),
    "electron": (
        "conditional",
        [
            "Electron/Chromium app detected. Accessibility may need to be enabled.",
            "Canvas-rendered content requires OCR — no accessibility elements exist.",
        ],
    ),
    "chromium_browser": (
        "conditional",
        [
            "Chromium-based browser detected.",
            "Use accessibility tree for standard page elements; canvas/WebGL content needs OCR.",
        ],
    ),
    "qt": (
        "partial",
        [
            "Qt app detected. Standard widgets accessible via AXUIElement.",
            "Custom QWidgets without QAccessibleInterface are invisible — use OCR fallback.",
        ],
    ),
    "java": (
        "partial",
        [
            "Java app detected. Accessibility depends on the Java Accessibility Bridge.",
            "Use OCR as primary fallback for Swing applications.",
        ],
    ),
    "terminal": (
        "full",
        [
            "Terminal app detected. Input goes through pyautogui passthrough.",
            "No special framework handling needed.",
        ],
    ),
    "unknown": (
        "unknown",
        [
            "Unknown UI framework. Try find_element first; fall back to OCR if needed.",
        ],
    ),
}


def detect_framework_darwin(window_title: str | None = None) -> dict:
    """Detect the UI framework of a macOS app by bundle identifier.

    Returns a dict matching the format of do_detect_framework():
        framework, uia_support, hints, process_name, class_name
    """
    info = _get_app_info_for_title(window_title)
    if info is None:
        return {
            "framework": "unknown",
            "uia_support": "unknown",
            "hints": ["No window found to inspect."],
            "process_name": "",
            "class_name": "",
        }

    bundle_id = info["bundle_id"]
    process_name = info["process_name"]
    framework = None

    # Check bundle-based classification
    if bundle_id in _COCOA_BUNDLES or bundle_id.startswith("com.apple."):
        framework = "cocoa"
    elif bundle_id in _ELECTRON_BUNDLES:
        framework = "electron"
    elif bundle_id in _BROWSER_BUNDLES:
        framework = "chromium_browser"
    elif bundle_id in _TERMINAL_BUNDLES:
        framework = "terminal"
    else:
        # Prefix matching
        for prefix in _QT_BUNDLES:
            if bundle_id.startswith(prefix):
                framework = "qt"
                break
        if not framework:
            for prefix in _JAVA_BUNDLES:
                if bundle_id.startswith(prefix):
                    framework = "java"
                    break

    if not framework:
        # Heuristic fallback
        if "electron" in process_name.lower():
            framework = "electron"
        else:
            framework = "unknown"

    uia_support, hints = _DARWIN_FRAMEWORK_INFO.get(
        framework, ("unknown", ["Framework not recognized."])
    )

    return {
        "framework": framework,
        "uia_support": uia_support,
        "hints": hints,
        "process_name": process_name,
        "class_name": "",  # No class_name concept on macOS
    }


# ---------------------------------------------------------------------------
# Role mapping: Windows UIA names <-> macOS AX names (Task 6)
# ---------------------------------------------------------------------------

_ROLE_WIN_TO_AX = {
    "Button": "AXButton",
    "Edit": "AXTextField",
    "MenuItem": "AXMenuItem",
    "TabItem": "AXRadioButton",
    "ComboBox": "AXPopUpButton",
    "CheckBox": "AXCheckBox",
    "List": "AXList",
    "Spinner": "AXIncrementor",
    "Text": "AXStaticText",
    "Hyperlink": "AXLink",
    "Image": "AXImage",
    "Group": "AXGroup",
    "Window": "AXWindow",
    "Slider": "AXSlider",
    "ProgressBar": "AXProgressIndicator",
    "RadioButton": "AXRadioButton",
    "ScrollBar": "AXScrollBar",
    "Table": "AXTable",
    "Tree": "AXOutline",
    "ToolBar": "AXToolbar",
    "StatusBar": "AXGroup",
}

_ROLE_AX_TO_WIN = {v: k for k, v in _ROLE_WIN_TO_AX.items()}
_ROLE_AX_TO_WIN["AXTextArea"] = "Edit"
_ROLE_AX_TO_WIN["AXComboBox"] = "ComboBox"
_ROLE_AX_TO_WIN["AXOutline"] = "Tree"


def windows_role_to_ax(role: str) -> str:
    """Convert a Windows UIA role name to macOS AX role name."""
    return _ROLE_WIN_TO_AX.get(role, f"AX{role}")


def ax_role_to_windows(ax_role: str) -> str:
    """Convert a macOS AX role name to Windows UIA role name."""
    if ax_role in _ROLE_AX_TO_WIN:
        return _ROLE_AX_TO_WIN[ax_role]
    return ax_role[2:] if ax_role.startswith("AX") else ax_role


# ---------------------------------------------------------------------------
# AXUIElement helpers (Task 6)
# ---------------------------------------------------------------------------


def ax_element_to_dict(element) -> dict | None:
    """Convert an AXUIElement to a serializable dict matching pywinauto format."""
    try:
        from ApplicationServices import (  # type: ignore[import-untyped]
            AXUIElementCopyAttributeValue,
        )
    except ImportError:
        return None

    def _get(attr):
        err, val = AXUIElementCopyAttributeValue(element, attr, None)
        return val if err == 0 else None

    role = _get("AXRole") or ""
    title = _get("AXTitle") or ""
    value = _get("AXValue")
    pos = _get("AXPosition")
    size = _get("AXSize")

    try:
        if pos is not None and size is not None:
            x = int(pos.x)
            y = int(pos.y)
            w = int(size.width)
            h = int(size.height)
        else:
            x = y = w = h = 0
    except (AttributeError, TypeError):
        x = y = w = h = 0

    value_str = str(value) if value is not None else ""

    return {
        "name": title if title else value_str,
        "role": ax_role_to_windows(role),
        "x": x,
        "y": y,
        "width": w,
        "height": h,
        "value": value_str,
    }


def ax_get_frontmost_app():
    """Get AXUIElement for the frontmost application."""
    try:
        from AppKit import NSWorkspace  # type: ignore[import-untyped]
        from ApplicationServices import AXUIElementCreateApplication  # type: ignore[import-untyped]
    except ImportError:
        return None

    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return None
    return AXUIElementCreateApplication(app.processIdentifier())


def ax_get_app_for_title(title: str):
    """Find app AXUIElement by window title search."""
    try:
        from Quartz import (  # type: ignore[import-untyped]
            kCGWindowName,
            kCGWindowOwnerName,
            kCGWindowOwnerPID,
            kCGWindowLayer,
        )
        from ApplicationServices import AXUIElementCreateApplication  # type: ignore[import-untyped]
    except ImportError:
        return None

    title_lower = title.lower()
    for w in _cg_window_list():
        if w.get(kCGWindowLayer, -1) != 0:
            continue
        win_name = w.get(kCGWindowName, "") or ""
        owner_name = w.get(kCGWindowOwnerName, "") or ""
        if title_lower in win_name.lower() or title_lower in owner_name.lower():
            pid = w.get(kCGWindowOwnerPID)
            if pid:
                return AXUIElementCreateApplication(pid)
    return None


def ax_find_elements(root, name: str = None, role: str = None, max_depth: int = 5) -> list[dict]:
    """Recursively find AX elements matching name/role criteria.

    Parameters
    ----------
    root : AXUIElement
        The root element to search from.
    name : str, optional
        Partial name to match (case-insensitive).
    role : str, optional
        Windows-style role name to match (e.g. "Button", not "AXButton").
    max_depth : int
        Maximum tree traversal depth.

    Returns a list of element dicts.
    """
    results: list[dict] = []
    _ax_walk(root, name, role, results, depth=0, max_depth=max_depth)
    return results


def _ax_walk(element, name, role, results, depth, max_depth):
    """Recursive AX tree walker."""
    if depth > max_depth:
        return

    try:
        from ApplicationServices import AXUIElementCopyAttributeValue  # type: ignore[import-untyped]
    except ImportError:
        return

    d = ax_element_to_dict(element)
    if d:
        name_match = (name is None) or (name.lower() in d["name"].lower())
        role_match = (role is None) or (role.lower() == d["role"].lower())
        if name_match and role_match and (d["name"] or d["role"]):
            results.append(d)

    # Get children
    err, children = AXUIElementCopyAttributeValue(element, "AXChildren", None)
    if err != 0 or not children:
        return

    for child in children:
        _ax_walk(child, name, role, results, depth + 1, max_depth)


def ax_get_focused_element() -> dict:
    """Get the currently focused UI element via AXFocusedUIElement."""
    try:
        from ApplicationServices import (  # type: ignore[import-untyped]
            AXUIElementCreateSystemWide,
            AXUIElementCopyAttributeValue,
        )
    except ImportError:
        return {"found": False, "error": "ApplicationServices not available"}

    system = AXUIElementCreateSystemWide()
    err, focused = AXUIElementCopyAttributeValue(
        system, "AXFocusedUIElement", None
    )
    if err != 0 or focused is None:
        return {"found": False, "error": "Cannot get focused element (accessibility permission may be required)"}

    d = ax_element_to_dict(focused)
    if d:
        return {"found": True, "element": d}
    return {"found": False, "error": "Focused element has no accessible info"}


# ---------------------------------------------------------------------------
# Not yet implemented -- stubs for future tasks
# ---------------------------------------------------------------------------


def run_ocr_native(image_path: str) -> list[dict]:
    """Run OCR on an image file using macOS Vision framework.

    Uses VNRecognizeTextRequest with accurate recognition level.
    Returns word-level dicts: {text, x, y, width, height}

    Vision coordinates are normalized (0.0-1.0) with origin at BOTTOM-LEFT.
    This function converts to pixel coordinates with origin at TOP-LEFT.
    """
    try:
        import Vision  # type: ignore[import-untyped]
        from Quartz import (  # type: ignore[import-untyped]
            CGImageSourceCreateWithURL,
            CGImageSourceCreateImageAtIndex,
        )
        from Foundation import NSURL  # type: ignore[import-untyped]
    except ImportError:
        return []

    from PIL import Image as PILImage

    # Get image dimensions
    try:
        with PILImage.open(image_path) as img:
            img_width, img_height = img.size
    except Exception:
        return []

    # Load as CGImage
    url = NSURL.fileURLWithPath_(image_path)
    source = CGImageSourceCreateWithURL(url, None)
    if source is None:
        return []
    cg_image = CGImageSourceCreateImageAtIndex(source, 0, None)
    if cg_image is None:
        return []

    # Create and execute text recognition request
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(1)  # 1 = VNRequestTextRecognitionLevelAccurate
    request.setUsesLanguageCorrection_(True)

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(
        cg_image, None
    )

    success, error = handler.performRequests_error_([request], None)
    if not success:
        return []

    results = request.results()
    if not results:
        return []

    words = []
    for observation in results:
        text = observation.text()
        if not text:
            continue
        bbox = observation.boundingBox()  # normalized, bottom-left origin

        # Convert normalized coords to pixel coords (top-left origin)
        line_x = int(bbox.origin.x * img_width)
        line_y = int((1.0 - bbox.origin.y - bbox.size.height) * img_height)
        line_w = int(bbox.size.width * img_width)
        line_h = int(bbox.size.height * img_height)

        # Split line into words with proportional bounding boxes
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

    return words


def get_loaded_modules(pid) -> list[str]:
    raise NotImplementedError("darwin_backend: implement in Task 8")

