"""Input tools -- click, type, send keys, scroll, drag, hover.

Provides core input functions backed by PyAutoGUI with smart routing
to Win32 console APIs for console windows. Registers MCP tools on a
Server instance.
"""

import sys
from typing import List, Optional

import pyautogui

from openowl.tools.window_classify import get_foreground_type, classify_window
from openowl.tools.session_context import ActionRecord

pyautogui.FAILSAFE = False  # disabled — virtual desktop isolation is the safety net
pyautogui.PAUSE = 0.05      # tightened — we have our own safety layer

# Win32-only: ctypes for console API (WriteConsoleInputW, AttachConsole, etc.)
# ctypes.wintypes may not be available on all non-Windows platforms, and
# ctypes.windll only exists on Windows.
if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes
    _HAS_WIN32 = True
else:
    _HAS_WIN32 = False

# Virtual key code mapping for special keys
_VK_MAP = {
    "enter": 0x0D, "return": 0x0D,
    "tab": 0x09,
    "escape": 0x1B, "esc": 0x1B,
    "backspace": 0x08,
    "delete": 0x2E,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
    "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
    "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}

STD_INPUT_HANDLE = -10
KEY_EVENT = 0x0001
WM_CHAR = 0x0102


if _HAS_WIN32:
    def _make_key_event(char_or_vk: int, is_char: bool = True, key_down: bool = True):
        """Create a KEY_EVENT_RECORD for WriteConsoleInputW."""

        class KEY_EVENT_RECORD(ctypes.Structure):
            _fields_ = [
                ("bKeyDown", ctypes.wintypes.BOOL),
                ("wRepeatCount", ctypes.wintypes.WORD),
                ("wVirtualKeyCode", ctypes.wintypes.WORD),
                ("wVirtualScanCode", ctypes.wintypes.WORD),
                ("UnicodeChar", ctypes.c_wchar),
                ("dwControlKeyState", ctypes.wintypes.DWORD),
            ]

        class INPUT_RECORD(ctypes.Structure):
            class _Event(ctypes.Union):
                _fields_ = [("KeyEvent", KEY_EVENT_RECORD)]
            _fields_ = [
                ("EventType", ctypes.wintypes.WORD),
                ("Event", _Event),
            ]

        rec = INPUT_RECORD()
        rec.EventType = KEY_EVENT
        rec.Event.KeyEvent.bKeyDown = key_down
        rec.Event.KeyEvent.wRepeatCount = 1
        if is_char:
            rec.Event.KeyEvent.UnicodeChar = chr(char_or_vk)
            rec.Event.KeyEvent.wVirtualKeyCode = 0
        else:
            rec.Event.KeyEvent.wVirtualKeyCode = char_or_vk
            rec.Event.KeyEvent.UnicodeChar = '\0'
        rec.Event.KeyEvent.wVirtualScanCode = 0
        rec.Event.KeyEvent.dwControlKeyState = 0
        return rec


def _send_text_to_console(pid: int, text: str, hwnd: int = 0) -> dict:
    """Send text to a console process via WriteConsoleInputW.

    Falls back to SendMessage(WM_CHAR) if AttachConsole fails (e.g., elevated console).
    """
    if not _HAS_WIN32:
        return {"success": False, "error": "Windows only"}

    # Try AttachConsole first
    ctypes.windll.kernel32.FreeConsole()
    if ctypes.windll.kernel32.AttachConsole(pid):
        try:
            handle = ctypes.windll.kernel32.GetStdHandle(STD_INPUT_HANDLE)
            written = ctypes.wintypes.DWORD()
            for ch in text:
                # Key down
                rec_down = _make_key_event(ord(ch), is_char=True, key_down=True)
                ctypes.windll.kernel32.WriteConsoleInputW(
                    handle, ctypes.byref(rec_down), 1, ctypes.byref(written)
                )
                # Key up
                rec_up = _make_key_event(ord(ch), is_char=True, key_down=False)
                ctypes.windll.kernel32.WriteConsoleInputW(
                    handle, ctypes.byref(rec_up), 1, ctypes.byref(written)
                )
            return {"success": True, "method": "WriteConsoleInput", "chars": len(text)}
        finally:
            ctypes.windll.kernel32.FreeConsole()
    else:
        # Fallback: SendMessage WM_CHAR (works cross-elevation)
        if not hwnd:
            return {"success": False, "error": "AttachConsole failed and no hwnd for SendMessage"}
        for ch in text:
            ctypes.windll.user32.SendMessageW(hwnd, WM_CHAR, ord(ch), 0)
        return {"success": True, "method": "SendMessage", "chars": len(text)}


def _send_keys_to_console(pid: int, keys: str, hwnd: int = 0) -> dict:
    """Send special keys (enter, tab, arrows, etc.) to a console process."""
    if not _HAS_WIN32:
        return {"success": False, "error": "Windows only"}

    parts = parse_hotkey(keys)

    ctypes.windll.kernel32.FreeConsole()
    if ctypes.windll.kernel32.AttachConsole(pid):
        try:
            handle = ctypes.windll.kernel32.GetStdHandle(STD_INPUT_HANDLE)
            written = ctypes.wintypes.DWORD()
            for key in parts:
                vk = _VK_MAP.get(key)
                if vk:
                    rec_down = _make_key_event(vk, is_char=False, key_down=True)
                    ctypes.windll.kernel32.WriteConsoleInputW(
                        handle, ctypes.byref(rec_down), 1, ctypes.byref(written)
                    )
                    rec_up = _make_key_event(vk, is_char=False, key_down=False)
                    ctypes.windll.kernel32.WriteConsoleInputW(
                        handle, ctypes.byref(rec_up), 1, ctypes.byref(written)
                    )
                else:
                    # Unknown key — try as single char
                    if len(key) == 1:
                        rec = _make_key_event(ord(key), is_char=True, key_down=True)
                        ctypes.windll.kernel32.WriteConsoleInputW(
                            handle, ctypes.byref(rec), 1, ctypes.byref(written)
                        )
            return {"success": True, "method": "WriteConsoleInput"}
        finally:
            ctypes.windll.kernel32.FreeConsole()
    else:
        return {"success": False, "error": "AttachConsole failed"}


# ------------------------------------------------------------------
# Helper / validation functions
# ------------------------------------------------------------------

def parse_hotkey(keys: str) -> List[str]:
    """Parse a hotkey string like ``'ctrl+s'`` into ``['ctrl', 's']``."""
    return [k.strip().lower() for k in keys.split("+")]


# macOS AppleScript keycodes for special keys
_APPLESCRIPT_KEYCODES = {
    "enter": 36, "return": 36, "tab": 48, "space": 49,
    "escape": 53, "esc": 53, "delete": 51, "backspace": 51,
    "up": 126, "down": 125, "left": 123, "right": 124,
    "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96,
    "f6": 97, "f7": 98, "f8": 100, "f9": 101, "f10": 109,
    "f11": 103, "f12": 111,
}

# Modifier name mapping to AppleScript "using" syntax
_APPLESCRIPT_MODIFIERS = {
    "command": "command down", "cmd": "command down",
    "control": "control down", "ctrl": "control down",
    "option": "option down", "alt": "option down",
    "shift": "shift down",
}


def _send_keys_applescript(keys: str) -> Optional[dict]:
    """Send keys via AppleScript System Events. Returns result dict or None to fall through.

    More reliable than pyautogui.hotkey() on macOS because it uses the
    accessibility system rather than raw CGEvents.
    """
    import subprocess as _sp
    parts = parse_hotkey(keys)

    # Separate modifiers from the actual key
    modifiers = []
    key_part = None
    for p in parts:
        if p in _APPLESCRIPT_MODIFIERS:
            modifiers.append(_APPLESCRIPT_MODIFIERS[p])
        else:
            key_part = p

    if key_part is None:
        return None  # All modifiers, no key — fall through

    # Build the AppleScript command
    using_clause = ""
    if modifiers:
        using_clause = f" using {{{', '.join(modifiers)}}}"

    if key_part in _APPLESCRIPT_KEYCODES:
        # Special key — use "key code"
        script = (
            f'tell application "System Events" to key code '
            f'{_APPLESCRIPT_KEYCODES[key_part]}{using_clause}'
        )
    elif len(key_part) == 1:
        # Single character — use "keystroke"
        script = (
            f'tell application "System Events" to keystroke '
            f'"{key_part}"{using_clause}'
        )
    else:
        return None  # Unknown key — fall through to pyautogui

    try:
        _sp.run(["osascript", "-e", script], capture_output=True, timeout=2)
    except Exception:
        return None  # Fall through to pyautogui on failure
    return {"action": "send_keys", "keys": keys, "method": "applescript"}


def validate_button(button: str) -> str:
    """Validate and normalise a mouse button name.

    Raises :class:`ValueError` if *button* is not one of
    ``left``, ``right``, ``middle``.
    """
    valid = ("left", "right", "middle")
    if button.lower() not in valid:
        raise ValueError(
            f"Invalid button '{button}'. Must be one of: {valid}"
        )
    return button.lower()


def validate_direction(direction: str) -> str:
    """Validate and normalise a scroll direction.

    Raises :class:`ValueError` if *direction* is not one of
    ``up``, ``down``, ``left``, ``right``.
    """
    valid = ("up", "down", "left", "right")
    if direction.lower() not in valid:
        raise ValueError(
            f"Invalid direction '{direction}'. Must be one of: {valid}"
        )
    return direction.lower()


# ------------------------------------------------------------------
# Core functions
# ------------------------------------------------------------------

def do_click(x: int, y: int, button: str = "left", clicks: int = 1) -> dict:
    """Click at (*x*, *y*) with the given *button* and *clicks* count.

    Coordinates are auto-scaled: if the last screenshot was downscaled
    (e.g. 1728→1280), coordinates in screenshot-space are automatically
    converted to screen-space. Coordinates already in screen-space
    (larger than the screenshot width) pass through unchanged.

    After clicking, checks if the foreground window title changed and
    captures a small region around the click point to detect visual changes.
    """
    import time
    from openowl.tools.target_window import ensure_focus
    from openowl.tools.screenshot import capture_screenshot, compare_screenshots, get_screenshot_scale, MAX_SCREENSHOT_WIDTH
    ensure_focus()
    button = validate_button(button)

    # Auto-scale: if coordinates look like screenshot-space (≤ MAX_SCREENSHOT_WIDTH),
    # multiply by the scale factor to get screen-space coordinates.
    scale = get_screenshot_scale()
    if scale > 1.0 and x <= MAX_SCREENSHOT_WIDTH:
        x = round(x * scale)
        y = round(y * scale)

    # Capture title before click for navigation detection
    from openowl.tools.windows import get_foreground_title
    pre_title = get_foreground_title()

    # Capture 400x400 region around click point before click
    half = 200
    region = {
        "x": max(0, x - half),
        "y": max(0, y - half),
        "w": 400,
        "h": 400,
    }
    before = capture_screenshot(region=region)

    pyautogui.click(x=x, y=y, button=button, clicks=clicks)

    time.sleep(0.5)

    # Re-focus target so we compare target app title, not terminal
    ensure_focus()

    # Check if foreground window changed
    post_title = get_foreground_title()

    # Capture same region after click
    after = capture_screenshot(region=region)
    pixel_diff = compare_screenshots(before["image"], after["image"])

    result = {
        "action": "click",
        "x": x,
        "y": y,
        "button": button,
        "clicks": clicks,
        "visual_change": pixel_diff >= 0.005,
        "pixel_diff": pixel_diff,
    }

    # Record in session context + workflow
    try:
        from openowl.tools.session_context import get_session
        get_session().record_click(x, y, visual_change=result["visual_change"])
    except Exception:
        pass
    try:
        from openowl.tools.workflow import is_recording, record_step
        if is_recording():
            record_step({"tool": "click", "args": {"x": x, "y": y, "button": button, "clicks": clicks}})
    except Exception:
        pass

    if pre_title and post_title and pre_title != post_title:
        result["navigation_warning"] = (
            f"Page navigated to \"{post_title[:80]}\". Press Alt+Left to go back if unintended."
        )

    # Query focused element so Claude knows what field it landed in
    try:
        from openowl.tools.ui_automation import do_get_focused_element
        focus = do_get_focused_element()
        if focus.get("found") and focus.get("element"):
            e = focus["element"]
            result["focused_element"] = {
                "role": e.get("role", ""),
                "name": e.get("name", ""),
                "value": e.get("value", ""),
            }
    except Exception:
        pass

    return result


def do_type_text(text: str, interval: float = 0.02) -> dict:
    """Type text — routes to Win32 console API for console windows, pyautogui otherwise."""
    from openowl.tools.target_window import ensure_focus
    ensure_focus()
    fg = get_foreground_type()
    if fg["type"] == "console":
        result = _send_text_to_console(fg["pid"], text, hwnd=fg["hwnd"])
        return {"action": "type_text", "text": text, "interval": interval, "method": result.get("method", "console")}
    pyautogui.write(text, interval=interval)
    # Record in session context + workflow
    try:
        from openowl.tools.session_context import get_session
        get_session().record_type(text)
    except Exception:
        pass
    try:
        from openowl.tools.workflow import is_recording, record_step
        if is_recording():
            record_step({"tool": "type_text", "args": {"text": text, "interval": interval}})
    except Exception:
        pass
    return {"action": "type_text", "text": text, "interval": interval, "method": "pyautogui"}


def do_send_keys(keys: str) -> dict:
    """Send keys — routes to Win32 console API for console windows, pyautogui otherwise."""
    from openowl.tools.target_window import ensure_focus
    ensure_focus()
    fg = get_foreground_type()
    if fg["type"] == "console" and _HAS_WIN32:
        parts = parse_hotkey(keys)
        # For single non-modifier keys, use console API
        if len(parts) == 1 and parts[0] in _VK_MAP:
            _send_keys_to_console(fg["pid"], keys, hwnd=fg["hwnd"])
            return {"action": "send_keys", "keys": keys, "method": "console"}
    # macOS: use AppleScript for key combos — pyautogui.hotkey() is unreliable
    # with modifier keys on macOS (sends raw CGEvents that get misinterpreted).
    if sys.platform == "darwin":
        result = _send_keys_applescript(keys)
        if result is not None:
            return result
    # Fall through to pyautogui for single keys or non-macOS
    parts = parse_hotkey(keys)
    if len(parts) == 1:
        pyautogui.press(parts[0])
    else:
        pyautogui.hotkey(*parts)
    # Record in session context + workflow
    try:
        from openowl.tools.session_context import get_session
        get_session().record_keys(keys)
    except Exception:
        pass
    try:
        from openowl.tools.workflow import is_recording, record_step
        if is_recording():
            record_step({"tool": "send_keys", "args": {"keys": keys}})
    except Exception:
        pass
    return {"action": "send_keys", "keys": keys, "method": "pyautogui"}


def do_scroll(
    x: int, y: int, direction: str,
    amount: Optional[int] = None,
    pages: Optional[float] = None,
) -> dict:
    """Move to (*x*, *y*) and scroll in *direction*.

    Specify *pages* (e.g. 1 = one full page, 0.5 = half page) for
    keyboard-based scrolling via PageDown/PageUp, which is DPI-independent
    and always scrolls exactly one page per press.

    Specify *amount* for raw mouse-wheel clicks.

    Defaults to ``pages=1`` (one full page) if neither is given.

    Captures before/after screenshots and compares to detect whether the
    scroll actually changed anything on screen.
    """
    import time
    from openowl.tools.target_window import ensure_focus
    from openowl.tools.screenshot import capture_screenshot, compare_screenshots, get_screenshot_scale, MAX_SCREENSHOT_WIDTH
    ensure_focus()
    direction = validate_direction(direction)

    # Auto-scale screenshot-space coords to screen-space
    scale = get_screenshot_scale()
    if scale > 1.0 and x <= MAX_SCREENSHOT_WIDTH:
        x = round(x * scale)
        y = round(y * scale)

    pyautogui.moveTo(x, y)

    # Capture before scroll
    before = capture_screenshot()

    use_keyboard = False
    if amount is not None:
        # Explicit wheel clicks requested — use mouse wheel
        if direction == "up":
            pyautogui.scroll(amount)
        elif direction == "down":
            pyautogui.scroll(-amount)
        elif direction == "left":
            pyautogui.hscroll(-amount)
        elif direction == "right":
            pyautogui.hscroll(amount)
    elif direction in ("left", "right"):
        # Horizontal scroll — no keyboard equivalent, use wheel
        wheel_amount = 50 if pages is None else max(1, int(50 * (pages or 1)))
        if direction == "left":
            pyautogui.hscroll(-wheel_amount)
        else:
            pyautogui.hscroll(wheel_amount)
    else:
        # Vertical page-based scroll — use PageDown/PageUp (DPI-independent)
        use_keyboard = True
        if pages is None:
            pages = 1.0
        key = "pagedown" if direction == "down" else "pageup"
        full_pages = int(pages)
        remainder = pages - full_pages
        # Press PageDown/PageUp for each full page
        for _ in range(full_pages):
            pyautogui.press(key)
            time.sleep(0.05)
        # For fractional pages (e.g. 0.5), use arrow key presses as approximation
        if remainder > 0:
            arrow = "down" if direction == "down" else "up"
            # ~30 arrow presses ≈ 1 page, scale by remainder
            arrow_presses = max(1, int(30 * remainder))
            for _ in range(arrow_presses):
                pyautogui.press(arrow)

    time.sleep(0.3)

    # Capture after scroll
    after = capture_screenshot()
    pixel_diff = compare_screenshots(before["image"], after["image"])

    result = {
        "action": "scroll",
        "x": x,
        "y": y,
        "direction": direction,
        "scroll_detected": pixel_diff >= 0.005,
        "pixel_diff": pixel_diff,
    }
    if use_keyboard:
        result["method"] = "keyboard"
        result["pages"] = pages
    else:
        result["method"] = "wheel"
        result["amount"] = amount

    if pixel_diff < 0.005:
        result["scroll_warning"] = (
            "Scroll had no effect — may have reached the end, or the wrong area received the scroll."
        )

    # Record in session context + workflow
    try:
        from openowl.tools.session_context import get_session
        get_session().record(ActionRecord(
            tool="scroll",
            summary=f"Scrolled {direction} at ({x},{y})",
            coordinates={"x": x, "y": y},
            visual_change=result["scroll_detected"],
        ))
    except Exception:
        pass
    try:
        from openowl.tools.workflow import is_recording, record_step
        if is_recording():
            record_step({"tool": "scroll", "args": {"x": x, "y": y, "direction": direction, "amount": amount, "pages": pages}})
    except Exception:
        pass

    return result


def do_drag(
    from_x: int,
    from_y: int,
    to_x: int,
    to_y: int,
    duration: float = 0.5,
) -> dict:
    """Drag from (*from_x*, *from_y*) to (*to_x*, *to_y*)."""
    from openowl.tools.target_window import ensure_focus
    from openowl.tools.screenshot import get_screenshot_scale, MAX_SCREENSHOT_WIDTH
    ensure_focus()

    # Auto-scale screenshot-space coords to screen-space
    scale = get_screenshot_scale()
    if scale > 1.0:
        if from_x <= MAX_SCREENSHOT_WIDTH:
            from_x = round(from_x * scale)
            from_y = round(from_y * scale)
        if to_x <= MAX_SCREENSHOT_WIDTH:
            to_x = round(to_x * scale)
            to_y = round(to_y * scale)

    pyautogui.moveTo(from_x, from_y)
    dx = to_x - from_x
    dy = to_y - from_y
    pyautogui.drag(dx, dy, duration=duration)
    return {
        "action": "drag",
        "from_x": from_x,
        "from_y": from_y,
        "to_x": to_x,
        "to_y": to_y,
        "duration": duration,
    }


def do_hover(x: int, y: int) -> dict:
    """Move the mouse to (*x*, *y*) without clicking."""
    from openowl.tools.target_window import ensure_focus
    from openowl.tools.screenshot import get_screenshot_scale, MAX_SCREENSHOT_WIDTH
    ensure_focus()

    # Auto-scale screenshot-space coords to screen-space
    scale = get_screenshot_scale()
    if scale > 1.0 and x <= MAX_SCREENSHOT_WIDTH:
        x = round(x * scale)
        y = round(y * scale)

    pyautogui.moveTo(x, y)
    return {
        "action": "hover",
        "x": x,
        "y": y,
    }


def do_paste_text(text: str) -> dict:
    """Paste text via clipboard — much faster than type_text for long content.

    Sets the clipboard, then sends Cmd+V (macOS) or Ctrl+V (others).
    Preserves the original clipboard content and restores it afterward.
    """
    import time
    import pyperclip
    from openowl.tools.target_window import ensure_focus
    ensure_focus()

    # Save original clipboard
    try:
        original = pyperclip.paste()
    except Exception:
        original = None

    # Set clipboard and paste
    pyperclip.copy(text)
    time.sleep(0.05)

    if sys.platform == "darwin":
        pyautogui.hotkey("command", "v")
    else:
        pyautogui.hotkey("ctrl", "v")

    time.sleep(0.2)

    # Restore original clipboard
    if original is not None:
        try:
            time.sleep(0.1)
            pyperclip.copy(original)
        except Exception:
            pass

    # Record in session context + workflow
    try:
        from openowl.tools.session_context import get_session
        preview = text[:50] + "..." if len(text) > 50 else text
        get_session().record(ActionRecord(
            tool="paste_text",
            summary=f'Pasted "{preview}"',
            target_text=text,
        ))
    except Exception:
        pass
    try:
        from openowl.tools.workflow import is_recording, record_step
        if is_recording():
            record_step({"tool": "paste_text", "args": {"text": text}})
    except Exception:
        pass

    return {"action": "paste_text", "length": len(text), "method": "clipboard"}


def do_get_mouse_position() -> dict:
    """Return the current mouse cursor coordinates."""
    x, y = pyautogui.position()
    return {"x": x, "y": y}


# ------------------------------------------------------------------
# MCP tool registration
# ------------------------------------------------------------------

def register(server) -> int:
    """Register the seven input MCP tools on *server*.

    Returns the number of tools registered (7).
    """
    import base64 as _b64
    from mcp.server.fastmcp import Image as McpImage
    from openowl.tools.safety import with_timeout, ActionTimeoutError

    @server.tool()
    def click(
        x: int,
        y: int,
        button: str = "left",
        clicks: int = 1,
    ) -> list:
        """Low-level click at pixel coordinates. LAST RESORT — prefer click_element(name=...) for buttons, fields, and links.

        Use click_element(name="Send") instead of guessing coordinates.
        Only use this for elements with no text label (e.g. color swatches, map pins).
        Coordinates are auto-scaled from screenshot-space to screen-space.

        Parameters:
            x: Horizontal pixel coordinate.
            y: Vertical pixel coordinate.
            button: Mouse button — "left", "right", or "middle".
            clicks: Number of clicks (1 = single, 2 = double).
        """
        try:
            result = with_timeout(
                lambda: do_click(x, y, button=button, clicks=clicks),
                timeout=5.0,
            )
        except ActionTimeoutError:
            return "Click timed out — the app may be unresponsive."

        diff_pct = result.get('pixel_diff', 0) * 100
        needs_screenshot = not result.get("visual_change", False) or result.get("navigation_warning")

        if result['clicks'] > 1:
            msg = "Double-clicked successfully."
        elif result['button'] == "right":
            msg = "Right-clicked — context menu should appear."
        else:
            msg = "Clicked successfully."
        if result.get("navigation_warning"):
            msg += f"\n{result['navigation_warning']}"
        if result.get("visual_change"):
            msg += "\nScreen updated."
        else:
            msg += "\nNothing changed on screen. Try clicking by element name or using keyboard navigation instead."
        # Report focused element so Claude can verify before typing
        fe = result.get("focused_element")
        if fe and (fe.get("role") or fe.get("name")):
            fname = fe.get("name", "")
            frole = fe.get("role", "")
            fval = fe.get("value", "")
            label = f'{frole} "{fname}"' if fname else frole
            if fval:
                label += f' (contains: "{fval[:80]}")'
            msg += f"\nNow focused on {label}."

        # Only include screenshot when something went wrong (saves ~$0.02/call in vision tokens)
        if needs_screenshot:
            from openowl.tools.screenshot import capture_screenshot
            shot = capture_screenshot()
            return [
                McpImage(data=_b64.b64decode(shot["image"]), format="png"),
                msg,
            ]

        return msg

    @server.tool()
    def type_text(
        text: str,
        interval: float = 0.02,
    ) -> str:
        """Type text character by character. For SHORT text only (passwords, search queries, filenames).

        For emails, messages, code, or any text >200 chars, use paste_text() instead —
        it's instant and avoids corruption from rapid keystroke simulation.

        Parameters:
            text: The string to type. Auto-redirects to paste_text if >200 chars.
            interval: Seconds between each keystroke (default 0.02).
        """
        # Auto-redirect long text to paste_text to prevent corruption
        if len(text) > 200:
            paste_result = with_timeout(
                lambda: do_paste_text(text),
                timeout=5.0,
            )
            return "Text was long — pasted via clipboard to avoid errors."
        try:
            result = with_timeout(
                lambda: do_type_text(text, interval=interval),
                timeout=10.0,
            )
        except ActionTimeoutError:
            # Timeout recovery: switch to paste
            try:
                paste_result = with_timeout(
                    lambda: do_paste_text(text),
                    timeout=5.0,
                )
                return "Typing was slow — recovered by pasting instead. Some partial text may have been typed before the paste."
            except Exception:
                return "Typing failed — try using paste_text() instead."
        return "Typed the text."

    @server.tool()
    def send_keys(keys: str) -> str:
        """Send a key or key-combination (e.g. 'enter', 'ctrl+s', 'ctrl+shift+p').

        Parameters:
            keys: Key name or combo joined with '+'.
        """
        try:
            result = with_timeout(
                lambda: do_send_keys(keys),
                timeout=5.0,
            )
        except ActionTimeoutError:
            return "Key press timed out — the app may be unresponsive."
        return f"Pressed {result['keys']}."

    @server.tool()
    def scroll(
        x: int,
        y: int,
        direction: str,
        amount: Optional[int] = None,
        pages: Optional[float] = None,
    ) -> list:
        """Scroll the mouse wheel at a screen position.

        Parameters:
            x: Horizontal pixel coordinate.
            y: Vertical pixel coordinate.
            direction: One of "up", "down", "left", "right".
            amount: Number of scroll clicks (default 50). Overrides amount.
            pages: Scroll by pages instead (1 = full page, 0.5 = half). Overrides amount.
        """
        try:
            result = with_timeout(
                lambda: do_scroll(x, y, direction, amount=amount, pages=pages),
                timeout=10.0,
            )
        except ActionTimeoutError:
            return "Scroll timed out — the app may be unresponsive."

        from openowl.tools.screenshot import capture_screenshot
        shot = capture_screenshot()
        diff_pct = result['pixel_diff'] * 100

        method = result.get("method", "wheel")
        if method == "keyboard":
            msg = f"Scrolled {result['direction']} {result['pages']} page(s)."
        else:
            msg = f"Scrolled {result['direction']} — content updated."
        if result.get("scroll_warning"):
            msg += f"\n{result['scroll_warning']}"

        return [
            McpImage(data=_b64.b64decode(shot["image"]), format="png"),
            msg,
        ]

    @server.tool()
    def drag(
        from_x: int,
        from_y: int,
        to_x: int,
        to_y: int,
        duration: float = 0.5,
    ) -> list:
        """Drag from one position to another. Returns a screenshot after dragging.

        Parameters:
            from_x: Start X coordinate.
            from_y: Start Y coordinate.
            to_x: End X coordinate.
            to_y: End Y coordinate.
            duration: How long the drag takes in seconds (default 0.5).
        """
        try:
            result = with_timeout(
                lambda: do_drag(from_x, from_y, to_x, to_y, duration=duration),
                timeout=5.0,
            )
        except ActionTimeoutError:
            return "Drag timed out — the app may be unresponsive."

        from openowl.tools.screenshot import capture_screenshot
        shot = capture_screenshot()

        return [
            McpImage(data=_b64.b64decode(shot["image"]), format="png"),
            "Dragged to the target position.",
        ]

    @server.tool()
    def hover(x: int, y: int) -> str:
        """Move the mouse cursor to a position without clicking. Useful for revealing tooltips.

        Parameters:
            x: Horizontal pixel coordinate.
            y: Vertical pixel coordinate.
        """
        try:
            result = with_timeout(
                lambda: do_hover(x, y),
                timeout=3.0,
            )
        except ActionTimeoutError:
            return "Hover timed out — the app may be unresponsive."
        return "Moved cursor to position."

    @server.tool()
    def paste_text(text: str) -> str:
        """PREFERRED way to input text into fields. Instant, reliable, no corruption.

        Use for: emails, messages, code, any text >200 chars, rich text editors
        (Gmail, Google Docs, Slack, Notion, etc.).

        Uses Cmd+V (macOS) or Ctrl+V (Windows/Linux) to paste via clipboard.
        Preserves and restores the original clipboard content.

        Only use type_text() instead for: passwords, search boxes, short form fields.

        Parameters:
            text: The text to paste.
        """
        try:
            result = with_timeout(
                lambda: do_paste_text(text),
                timeout=5.0,
            )
        except ActionTimeoutError:
            return "Paste timed out — the app may be unresponsive."
        return "Pasted the text."

    @server.tool()
    def get_mouse_position() -> str:
        """Get the current mouse cursor position.

        Returns the x, y pixel coordinates of the mouse cursor.
        Useful for debugging coordinate issues or confirming cursor placement.
        """
        try:
            result = with_timeout(do_get_mouse_position, timeout=2.0)
        except ActionTimeoutError:
            return "Timed out after 2s getting mouse position."
        return f"Cursor is at ({result['x']}, {result['y']})."

    return 8

