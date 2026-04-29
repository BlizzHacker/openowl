"""Window tools -- list, focus, and launch application windows.

Provides cross-platform window management using native APIs per platform:
    - Windows: Win32 via ctypes (EnumWindows, SetForegroundWindow, etc.)
    - macOS:   AppleScript via subprocess
    - Linux:   wmctrl via subprocess

Core functions:
    do_list_windows   - enumerate visible windows with geometry
    do_focus_window   - find window by partial title, perform action
    do_launch_app     - launch an application via subprocess.Popen

MCP registration:
    register          - wire list_windows, focus_window, launch_app on a Server
"""

import platform
import subprocess
import shlex
from typing import List, Optional


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def get_platform() -> str:
    """Return 'windows', 'darwin', or 'linux'."""
    p = platform.system().lower()
    if p == "windows":
        return "windows"
    elif p == "darwin":
        return "darwin"
    else:
        return "linux"


def validate_window_action(action: str) -> str:
    """Validate and normalise a window action string.

    Raises :class:`ValueError` if *action* is not one of
    ``focus``, ``minimize``, ``maximize``, ``restore``.
    """
    valid = ("focus", "minimize", "maximize", "restore")
    if action.lower() not in valid:
        raise ValueError(
            f"Invalid action '{action}'. Must be one of: {valid}"
        )
    return action.lower()


def get_foreground_title() -> str:
    """Return the title of the current foreground window.

    Returns an empty string on non-Windows platforms or on failure.
    """
    if get_platform() == "darwin":
        from openowl.owl_platform import get_foreground_title as _darwin_title
        return _darwin_title()
    if get_platform() != "windows":
        return ""
    import ctypes
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def find_matching_window(title: str, windows: list[dict]) -> dict:
    """Find a window by title using case-insensitive substring matching.

    Falls back to matching against the process executable name when no
    title match is found (e.g. query "steam" matches process "steam.exe").

    Returns:
        {"window": dict, "match_quality": "exact"|"process_name"} on match, or
        {"window": None, "available": [str, ...]} listing all window titles.
    """
    if not windows:
        return {"window": None, "available": []}

    title_lower = title.lower()
    available = [w["title"] for w in windows]

    # Substring match (case-insensitive)
    for win in windows:
        if title_lower in win["title"].lower():
            return {"window": win, "match_quality": "exact"}

    # Fallback: match against process executable name stem
    for win in windows:
        proc = win.get("process_name", "")
        if proc:
            stem = proc.rsplit(".", 1)[0].lower()  # "steam.exe" → "steam"
            if title_lower == stem or title_lower in stem:
                return {"window": win, "match_quality": "process_name"}

    return {"window": None, "available": available}


# ------------------------------------------------------------------
# Platform-specific: list windows
# ------------------------------------------------------------------

def _get_process_name_win32(pid: int) -> str:
    """Get the executable name for a process ID. Returns '' on failure."""
    import ctypes
    import ctypes.wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = ctypes.wintypes.DWORD(260)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            # Extract just the filename from the full path
            path = buf.value
            return path.rsplit("\\", 1)[-1] if "\\" in path else path
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _list_windows_win32() -> List[dict]:
    """List visible windows on Windows using Win32 EnumWindows via ctypes."""
    import ctypes
    import ctypes.wintypes

    user32 = ctypes.windll.user32

    EnumWindows = user32.EnumWindows
    EnumWindowsProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
    )
    GetWindowTextW = user32.GetWindowTextW
    GetWindowTextLengthW = user32.GetWindowTextLengthW
    IsWindowVisible = user32.IsWindowVisible
    GetWindowRect = user32.GetWindowRect
    GetWindowThreadProcessId = user32.GetWindowThreadProcessId

    windows = []

    def enum_callback(hwnd, _lparam):
        if not IsWindowVisible(hwnd):
            return True
        length = GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if not title:
            return True

        rect = ctypes.wintypes.RECT()
        GetWindowRect(hwnd, ctypes.byref(rect))

        # Get process name
        pid = ctypes.wintypes.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc_name = _get_process_name_win32(pid.value)

        windows.append({
            "title": title,
            "process_name": proc_name,
            "x": rect.left,
            "y": rect.top,
            "width": rect.right - rect.left,
            "height": rect.bottom - rect.top,
        })
        return True

    EnumWindows(EnumWindowsProc(enum_callback), 0)
    return windows


def _list_windows_darwin() -> List[dict]:
    """List visible windows on macOS using AppleScript."""
    script = '''
    tell application "System Events"
        set windowList to ""
        repeat with proc in (every process whose visible is true)
            repeat with w in (every window of proc)
                set windowList to windowList & name of w & "||" & ¬
                    position of w & "||" & size of w & linefeed
            end repeat
        end repeat
        return windowList
    end tell
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10
        )
        windows = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("||")
            if len(parts) >= 3:
                title = parts[0].strip()
                # Position comes as "x, y"
                pos_parts = parts[1].strip().split(", ")
                size_parts = parts[2].strip().split(", ")
                try:
                    windows.append({
                        "title": title,
                        "x": int(pos_parts[0]),
                        "y": int(pos_parts[1]),
                        "width": int(size_parts[0]),
                        "height": int(size_parts[1]),
                    })
                except (ValueError, IndexError):
                    continue
        return windows
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def _list_windows_linux() -> List[dict]:
    """List visible windows on Linux using wmctrl -lG."""
    try:
        result = subprocess.run(
            ["wmctrl", "-lG"],
            capture_output=True, text=True, timeout=10
        )
        windows = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            # wmctrl -lG format:
            # 0x04000007  0 100  200  800  600  hostname Title goes here
            parts = line.split(None, 7)
            if len(parts) >= 8:
                try:
                    windows.append({
                        "title": parts[7],
                        "x": int(parts[2]),
                        "y": int(parts[3]),
                        "width": int(parts[4]),
                        "height": int(parts[5]),
                    })
                except (ValueError, IndexError):
                    continue
        return windows
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


# ------------------------------------------------------------------
# Platform-specific: focus window
# ------------------------------------------------------------------

def _focus_window_win32(title: str, action: str) -> dict:
    """Focus/minimize/maximize/restore a window on Windows via Win32."""
    import ctypes
    import ctypes.wintypes

    user32 = ctypes.windll.user32

    EnumWindows = user32.EnumWindows
    EnumWindowsProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
    )
    GetWindowTextW = user32.GetWindowTextW
    GetWindowTextLengthW = user32.GetWindowTextLengthW
    IsWindowVisible = user32.IsWindowVisible
    SetForegroundWindow = user32.SetForegroundWindow
    ShowWindow = user32.ShowWindow

    SW_MINIMIZE = 6
    SW_MAXIMIZE = 3
    SW_RESTORE = 9

    target_hwnd = None
    target_title = None
    title_lower = title.lower()

    # Collect all visible windows so we can use find_matching_window()
    # which supports both title substring and process-name fallback.
    all_hwnds = []  # parallel list of hwnds for each window dict

    def enum_callback(hwnd, _lparam):
        if not IsWindowVisible(hwnd):
            return True
        length = GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        GetWindowTextW(hwnd, buf, length + 1)
        win_title = buf.value
        if not win_title:
            return True

        # Get process name for fallback matching
        GetWindowThreadProcessId = user32.GetWindowThreadProcessId
        pid = ctypes.wintypes.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc_name = _get_process_name_win32(pid.value)

        all_hwnds.append((hwnd, {
            "title": win_title,
            "process_name": proc_name,
        }))
        return True

    EnumWindows(EnumWindowsProc(enum_callback), 0)

    # Use shared matching logic (title substring + process-name fallback)
    window_dicts = [info for _, info in all_hwnds]
    match = find_matching_window(title, window_dicts)

    if match["window"] is not None:
        matched_title = match["window"]["title"]
        # Find the hwnd for the matched window
        for hwnd, info in all_hwnds:
            if info["title"] == matched_title:
                target_hwnd = hwnd
                target_title = matched_title
                break

    if target_hwnd is None:
        available = match.get("available", [])
        return {"success": False, "error": f"No window matching '{title}' found"}

    if action == "focus":
        # Plain SetForegroundWindow often fails if we don't own the
        # foreground lock.  The reliable pattern:
        #   1. Attach to the foreground thread's input queue
        #   2. Bring the window to top
        #   3. SetForegroundWindow
        #   4. Detach
        # This avoids the Alt-key hack that can pop the Start menu.
        GetForegroundWindow = user32.GetForegroundWindow
        GetWindowThreadProcessId = user32.GetWindowThreadProcessId
        GetCurrentThreadId = ctypes.windll.kernel32.GetCurrentThreadId
        AttachThreadInput = user32.AttachThreadInput
        BringWindowToTop = user32.BringWindowToTop

        fg_hwnd = GetForegroundWindow()
        fg_tid = GetWindowThreadProcessId(fg_hwnd, None)
        our_tid = GetCurrentThreadId()

        if fg_tid != our_tid:
            AttachThreadInput(our_tid, fg_tid, True)

        # If the window is minimized, restore it first
        SW_SHOW = 5
        ShowWindow(target_hwnd, SW_RESTORE)
        BringWindowToTop(target_hwnd)
        SetForegroundWindow(target_hwnd)

        if fg_tid != our_tid:
            AttachThreadInput(our_tid, fg_tid, False)

        # Poll until the target is actually foreground, or bail after 500ms.
        # Without this, the terminal can steal focus back before input lands.
        import time
        for _ in range(50):
            time.sleep(0.01)
            if GetForegroundWindow() == target_hwnd:
                break
    elif action == "minimize":
        ShowWindow(target_hwnd, SW_MINIMIZE)
    elif action == "maximize":
        ShowWindow(target_hwnd, SW_MAXIMIZE)
    elif action == "restore":
        ShowWindow(target_hwnd, SW_RESTORE)

    return {"success": True, "window": target_title, "action": action}


def _focus_window_darwin(title: str, action: str) -> dict:
    """Focus/minimize/maximize/restore a window on macOS via AppleScript."""
    title_escaped = title.replace('"', '\\"')

    if action == "focus":
        script = f'''
        tell application "System Events"
            set targetProc to first process whose visible is true and ¬
                (name of every window contains "{title_escaped}")
            set frontmost of targetProc to true
        end tell
        '''
    elif action == "minimize":
        script = f'''
        tell application "System Events"
            repeat with proc in (every process whose visible is true)
                repeat with w in (every window of proc)
                    if name of w contains "{title_escaped}" then
                        click (first button of w whose subrole is "AXMinimizeButton")
                        return "done"
                    end if
                end repeat
            end repeat
        end tell
        '''
    elif action == "maximize":
        script = f'''
        tell application "System Events"
            repeat with proc in (every process whose visible is true)
                repeat with w in (every window of proc)
                    if name of w contains "{title_escaped}" then
                        click (first button of w whose subrole is "AXZoomButton")
                        return "done"
                    end if
                end repeat
            end repeat
        end tell
        '''
    elif action == "restore":
        # On macOS, "restore" is essentially un-minimize
        script = f'''
        tell application "System Events"
            repeat with proc in (every process whose visible is true)
                repeat with w in (every window of proc)
                    if name of w contains "{title_escaped}" then
                        set frontmost of proc to true
                        return "done"
                    end if
                end repeat
            end repeat
        end tell
        '''
    else:
        return {"success": False, "error": f"Unknown action: {action}"}

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return {"success": True, "window": title, "action": action}
        else:
            return {
                "success": False,
                "error": result.stderr.strip() or f"No window matching '{title}' found",
            }
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return {"success": False, "error": str(exc)}


def _focus_window_linux(title: str, action: str) -> dict:
    """Focus/minimize/maximize/restore a window on Linux via wmctrl."""
    try:
        if action == "focus":
            result = subprocess.run(
                ["wmctrl", "-a", title],
                capture_output=True, text=True, timeout=10
            )
        elif action == "minimize":
            # wmctrl doesn't have a direct minimize; use xdotool fallback
            result = subprocess.run(
                ["xdotool", "search", "--name", title, "windowminimize"],
                capture_output=True, text=True, timeout=10
            )
        elif action == "maximize":
            result = subprocess.run(
                ["wmctrl", "-r", title, "-b", "add,maximized_vert,maximized_horz"],
                capture_output=True, text=True, timeout=10
            )
        elif action == "restore":
            result = subprocess.run(
                ["wmctrl", "-r", title, "-b", "remove,maximized_vert,maximized_horz"],
                capture_output=True, text=True, timeout=10
            )
        else:
            return {"success": False, "error": f"Unknown action: {action}"}

        if result.returncode == 0:
            return {"success": True, "window": title, "action": action}
        else:
            return {
                "success": False,
                "error": result.stderr.strip() or f"No window matching '{title}' found",
            }
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return {"success": False, "error": str(exc)}


# ------------------------------------------------------------------
# Core functions
# ------------------------------------------------------------------

def do_list_windows() -> List[dict]:
    """Enumerate visible windows with geometry.

    Returns a list of dicts, each with keys:
    ``title``, ``x``, ``y``, ``width``, ``height``.
    """
    plat = get_platform()
    if plat == "windows":
        return _list_windows_win32()
    elif plat == "darwin":
        from openowl.owl_platform import list_windows_native
        return list_windows_native()
    else:
        return _list_windows_linux()


def do_focus_window(title: str, action: str = "focus") -> dict:
    """Find a window by partial *title* match and perform *action*.

    Parameters
    ----------
    title : str
        Partial window title to search for (case-insensitive match).
    action : str
        One of ``focus``, ``minimize``, ``maximize``, ``restore``.

    Returns
    -------
    dict
        ``{"success": True, "window": str, "action": str}`` on success,
        ``{"success": False, "error": str}`` on failure.
    """
    action = validate_window_action(action)
    plat = get_platform()
    if plat == "windows":
        return _focus_window_win32(title, action)
    elif plat == "darwin":
        from openowl.owl_platform import focus_window_native
        return focus_window_native(title, action)
    else:
        return _focus_window_linux(title, action)


def do_launch_app(path: str, args: Optional[str] = None) -> dict:
    """Launch an application at *path* with optional *args*.

    Parameters
    ----------
    path : str
        Path or name of the executable to launch.
    args : str | None
        Space-separated arguments to pass to the executable.

    Returns
    -------
    dict
        ``{"success": True, "pid": int}`` on success,
        ``{"success": False, "error": str}`` on failure.
    """
    try:
        # macOS: .app bundles are directories, must use 'open' command
        if get_platform() == "darwin" and path.endswith(".app"):
            cmd = ["open", "-a", path]
            if args:
                cmd.append("--args")
                cmd.extend(shlex.split(args))
        else:
            cmd = [path]
            if args:
                cmd.extend(shlex.split(args))
        proc = subprocess.Popen(cmd)
        return {"success": True, "pid": proc.pid}
    except (FileNotFoundError, OSError) as exc:
        return {"success": False, "error": str(exc)}


# ------------------------------------------------------------------
# MCP tool registration
# ------------------------------------------------------------------

def register(server) -> int:
    """Register *list_windows*, *focus_window*, and *launch_app* tools.

    Returns the number of tools registered (3).
    """

    from openowl.tools.safety import with_timeout, ActionTimeoutError

    @server.tool()
    def list_windows() -> str:
        """List all visible windows with their titles and positions."""
        try:
            windows = with_timeout(do_list_windows, timeout=5.0)
        except ActionTimeoutError:
            return "Listing windows timed out — the system may be unresponsive."
        if not windows:
            return "No visible windows found."
        lines = []
        for w in windows:
            lines.append(f"  {w['title']}")
        return f"{len(windows)} windows open:\n" + "\n".join(lines)

    @server.tool()
    def focus_window(title: str, action: str = "focus") -> str:
        """Find a window by partial title and focus, minimize, maximize, or restore it.

        Parameters:
            title: Partial window title to search for.
            action: One of "focus", "minimize", "maximize", "restore" (default "focus").
        """
        try:
            result = with_timeout(
                lambda: do_focus_window(title, action=action),
                timeout=5.0,
            )
        except ActionTimeoutError:
            return f"Window operation timed out — the app may be unresponsive."
        if result["success"]:
            return f"{result['action'].capitalize()}ed window \"{result['window']}\"."
        return f"Failed: {result['error']}"

    @server.tool()
    def launch_app(path: str, args: str = "") -> str:
        """Launch an application.

        Parameters:
            path: Path or name of the executable to launch.
            args: Space-separated arguments (default "").
        """
        result = do_launch_app(path, args=args if args else None)
        if result["success"]:
            return f"Launched {path}."
        return f"Could not launch {path} — {result['error']}"

    return 3

