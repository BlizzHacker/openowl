"""Window type classification for smart input routing.

Classifies the foreground window as one of:
    console  - Classic cmd/PowerShell (ConsoleWindowClass)
    terminal - Windows Terminal (modern, handles pyautogui fine)
    browser  - Edge, Chrome, Firefox
    electron - VS Code, Slack, Discord, etc.
    generic  - Everything else

Used by input_tools (routing), safety (desktop guard), and ui_automation.
"""

import platform
import sys
import time
from typing import Optional

# Classification constants
CONSOLE_CLASSES = {"ConsoleWindowClass"}
TERMINAL_PROCESSES = {"WindowsTerminal.exe", "wt.exe"}
BROWSER_PROCESSES = {"msedge.exe", "chrome.exe", "firefox.exe", "brave.exe", "opera.exe"}
ELECTRON_PROCESSES = {"Code.exe", "Slack.exe", "Discord.exe", "Obsidian.exe",
                      "Notion.exe", "Postman.exe", "GitHubDesktop.exe"}

# Cache: hwnd -> (result, timestamp)
_cache: dict[int, tuple[dict, float]] = {}
_CACHE_TTL = 2.0  # seconds


def _get_class_name(hwnd: int) -> str:
    """Get the window class name via Win32 GetClassName."""
    if platform.system().lower() != "windows":
        return ""
    import ctypes
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _get_process_name(hwnd: int) -> str:
    """Get the process executable name from a window handle."""
    if platform.system().lower() != "windows":
        return ""
    import ctypes
    import ctypes.wintypes
    pid = ctypes.wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    # Open process and query name
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
    )
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = ctypes.wintypes.DWORD(260)
        ctypes.windll.kernel32.QueryFullProcessImageNameW(
            handle, 0, buf, ctypes.byref(size)
        )
        # Extract just the filename
        path = buf.value
        return path.rsplit("\\", 1)[-1] if "\\" in path else path
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _is_elevated_process(pid: int) -> bool:
    """Check if a process is running elevated (admin)."""
    if platform.system().lower() != "windows":
        return False
    import ctypes
    import ctypes.wintypes
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid
    )
    if not handle:
        return False  # Can't open = likely elevated and we're not
    try:
        token = ctypes.wintypes.HANDLE()
        if not ctypes.windll.advapi32.OpenProcessToken(
            handle, 0x0008, ctypes.byref(token)  # TOKEN_QUERY
        ):
            return False
        try:
            # TokenElevation = 20
            elevation = ctypes.wintypes.DWORD()
            size = ctypes.wintypes.DWORD()
            ctypes.windll.advapi32.GetTokenInformation(
                token, 20, ctypes.byref(elevation),
                ctypes.sizeof(elevation), ctypes.byref(size)
            )
            return elevation.value != 0
        finally:
            ctypes.windll.kernel32.CloseHandle(token)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _get_foreground_hwnd() -> int:
    """Get the foreground window handle."""
    if platform.system().lower() != "windows":
        return 0
    import ctypes
    return ctypes.windll.user32.GetForegroundWindow()


def classify_window(hwnd: int) -> dict:
    """Classify a window by its handle.

    Returns dict with keys: type, process_name, class_name, hwnd, pid, is_elevated
    """
    now = time.monotonic()

    # Check cache
    if hwnd in _cache:
        result, ts = _cache[hwnd]
        if now - ts < _CACHE_TTL:
            return result

    class_name = _get_class_name(hwnd)
    process_name = _get_process_name(hwnd)

    # Get PID
    pid = 0
    if platform.system().lower() == "windows":
        import ctypes
        import ctypes.wintypes
        _pid = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(_pid))
        pid = _pid.value

    # Classify
    if class_name in CONSOLE_CLASSES:
        win_type = "console"
    elif process_name in TERMINAL_PROCESSES:
        win_type = "terminal"
    elif process_name in BROWSER_PROCESSES:
        win_type = "browser"
    elif process_name in ELECTRON_PROCESSES:
        win_type = "electron"
    else:
        win_type = "generic"

    is_elevated = _is_elevated_process(pid) if pid else False

    result = {
        "type": win_type,
        "process_name": process_name,
        "class_name": class_name,
        "hwnd": hwnd,
        "pid": pid,
        "is_elevated": is_elevated,
    }

    _cache[hwnd] = (result, now)
    return result


def get_foreground_type() -> dict:
    """Convenience: classify the current foreground window."""
    if sys.platform == "darwin":
        from openowl.owl_platform import classify_window_native
        return classify_window_native()
    hwnd = _get_foreground_hwnd()
    return classify_window(hwnd)


def invalidate_cache():
    """Clear the classification cache. Called after focus_window changes."""
    _cache.clear()

