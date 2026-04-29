"""Virtual desktop tools -- create and manage isolated desktops.

Provides one MCP tool:
    virtual_desktop  - create, switch, or close virtual desktops

When Claude needs to interact with GUI applications, creating an isolated
virtual desktop prevents disturbing the user's windows and workspace.

The host terminal (the window running the MCP server) is automatically
pinned to all desktops on first use, so the user can always see Claude's
output regardless of which desktop is active.

Platform support:
    - Windows: Win+Ctrl hotkeys + pyvda for window pinning
    - macOS:   Ctrl+Left/Right for Spaces (limited -- no programmatic create/close)
    - Linux:   wmctrl workspace switching
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import time

import pyautogui

# Win32-only imports
if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes as wt

# Best-effort state tracking (resets on server restart)
_desktop_state = {
    "on_isolated": False,
    "desktops_created": 0,
}

# Cached host terminal HWND (found once, reused)
_host_terminal_hwnd = None
_terminal_pinned = False


def _get_platform() -> str:
    p = platform.system().lower()
    if p == "windows":
        return "windows"
    elif p == "darwin":
        return "darwin"
    return "linux"


def validate_desktop_action(action: str) -> str:
    valid = ("create", "switch_left", "switch_right", "close")
    if action.lower() not in valid:
        raise ValueError(f"Invalid action '{action}'. Must be one of: {valid}")
    return action.lower()


# ------------------------------------------------------------------
# Windows: find the host terminal window
# ------------------------------------------------------------------

def _find_host_terminal_hwnd() -> int | None:
    """Walk up the process tree from the MCP server to find the terminal HWND.

    Returns the HWND (as int) of the terminal window, or None.
    """
    if _get_platform() != "windows":
        return None

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # --- Build pid -> parent_pid map via CreateToolhelp32Snapshot ---
    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong),
            ("cntUsage", ctypes.c_ulong),
            ("th32ProcessID", ctypes.c_ulong),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", ctypes.c_ulong),
            ("cntThreads", ctypes.c_ulong),
            ("th32ParentProcessID", ctypes.c_ulong),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong),
            ("szExeFile", ctypes.c_char * 260),
        ]

    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == -1:
        return None

    pe = PROCESSENTRY32()
    pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
    pid_to_parent = {}

    if kernel32.Process32First(snapshot, ctypes.byref(pe)):
        pid_to_parent[pe.th32ProcessID] = pe.th32ParentProcessID
        while kernel32.Process32Next(snapshot, ctypes.byref(pe)):
            pid_to_parent[pe.th32ProcessID] = pe.th32ParentProcessID
    kernel32.CloseHandle(snapshot)

    # --- Walk up from current PID ---
    chain = []
    current = os.getpid()
    for _ in range(20):  # safety limit
        chain.append(current)
        parent = pid_to_parent.get(current)
        if parent is None or parent == 0 or parent == current:
            break
        current = parent

    # --- Find the first PID in the chain (oldest ancestor first) with a visible window ---
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    for target_pid in reversed(chain):
        found_hwnd = [None]

        def _callback(hwnd, _lparam, _pid=target_pid):
            if not user32.IsWindowVisible(hwnd):
                return True
            win_pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(win_pid))
            if win_pid.value != _pid:
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                found_hwnd[0] = hwnd
                return False  # stop enumeration
            return True

        user32.EnumWindows(WNDENUMPROC(_callback), 0)
        if found_hwnd[0]:
            return int(found_hwnd[0])

    return None


def _get_host_terminal() -> int | None:
    """Return the cached host terminal HWND, finding it if necessary."""
    from openowl.tools.safety import with_timeout

    global _host_terminal_hwnd
    if _host_terminal_hwnd is None:
        try:
            _host_terminal_hwnd = with_timeout(_find_host_terminal_hwnd, timeout=3.0)
        except Exception:
            pass
    return _host_terminal_hwnd


# ------------------------------------------------------------------
# Windows: pin the host terminal to all desktops via pyvda
# ------------------------------------------------------------------

def _pin_terminal():
    """Pin the host terminal to all virtual desktops (best-effort).

    Uses pyvda (wraps undocumented IVirtualDesktopPinnedApps COM).
    Only pins once per session.
    """
    from openowl.tools.safety import with_timeout

    global _terminal_pinned
    if _terminal_pinned:
        return

    hwnd = _get_host_terminal()
    if not hwnd:
        return

    def _do_pin():
        global _terminal_pinned
        import pyvda
        view = pyvda.AppView(hwnd)
        if not view.is_pinned():
            view.pin()
        _terminal_pinned = True

    try:
        with_timeout(_do_pin, timeout=3.0)
    except Exception:
        pass  # best-effort; terminal just won't follow


def _unpin_terminal():
    """Unpin the host terminal (called before closing the last desktop)."""
    from openowl.tools.safety import with_timeout

    global _terminal_pinned
    if not _terminal_pinned:
        return

    hwnd = _get_host_terminal()
    if not hwnd:
        return

    def _do_unpin():
        global _terminal_pinned
        import pyvda
        view = pyvda.AppView(hwnd)
        if view.is_pinned():
            view.unpin()
        _terminal_pinned = False

    try:
        with_timeout(_do_unpin, timeout=3.0)
    except Exception:
        pass


# ------------------------------------------------------------------
# Platform implementations
# ------------------------------------------------------------------

def _desktop_windows(action: str) -> str:
    global _desktop_state

    if action == "create":
        # Pin the terminal BEFORE creating the desktop so it follows us
        _pin_terminal()
        pyautogui.hotkey("win", "ctrl", "d")
        time.sleep(0.5)
        _desktop_state["on_isolated"] = True
        _desktop_state["desktops_created"] += 1
        return (
            f"Created virtual desktop #{_desktop_state['desktops_created']}. "
            f"You are now on an isolated desktop."
        )

    elif action == "switch_left":
        pyautogui.hotkey("win", "ctrl", "left")
        time.sleep(0.3)
        _desktop_state["on_isolated"] = False
        return "Switched to the desktop on the left."

    elif action == "switch_right":
        pyautogui.hotkey("win", "ctrl", "right")
        time.sleep(0.3)
        _desktop_state["on_isolated"] = True
        return "Switched to the desktop on the right."

    else:  # close
        if not _desktop_state["on_isolated"]:
            return (
                "Warning: You appear to be on the user's original desktop. "
                "Closing it may disrupt their work. "
                "Switch to the isolated desktop first with switch_right."
            )
        # Unpin before closing so the terminal returns to the original desktop
        _unpin_terminal()
        pyautogui.hotkey("win", "ctrl", "F4")
        time.sleep(0.5)
        _desktop_state["on_isolated"] = False
        _desktop_state["desktops_created"] = max(0, _desktop_state["desktops_created"] - 1)
        return "Closed the virtual desktop. Returned to the previous desktop."


def _desktop_darwin(action: str) -> str:
    if action == "create":
        return (
            "macOS does not support programmatic Space creation. "
            "Use Mission Control (Ctrl+Up or F3) to add a Space manually, "
            "then use switch_left/switch_right to navigate."
        )
    elif action == "switch_left":
        pyautogui.hotkey("ctrl", "left")
        time.sleep(0.5)
        return "Switched to the Space on the left."
    elif action == "switch_right":
        pyautogui.hotkey("ctrl", "right")
        time.sleep(0.5)
        return "Switched to the Space on the right."
    else:  # close
        return (
            "macOS does not support programmatic Space removal. "
            "Use Mission Control to close Spaces manually."
        )


def _desktop_linux(action: str) -> str:
    global _desktop_state

    if action == "create":
        return (
            "Linux workspace creation varies by desktop environment. "
            "Most have a fixed number of workspaces. "
            "Use switch_left/switch_right to navigate existing ones."
        )

    elif action == "switch_left":
        try:
            subprocess.run(["wmctrl", "-s", "0"],
                           capture_output=True, text=True, timeout=5)
            _desktop_state["on_isolated"] = False
            return "Switched to workspace 0."
        except FileNotFoundError:
            return "wmctrl not installed. Run: sudo apt install wmctrl"

    elif action == "switch_right":
        try:
            result = subprocess.run(["wmctrl", "-d"],
                                    capture_output=True, text=True, timeout=5)
            current = 0
            for line in result.stdout.splitlines():
                if "*" in line:
                    current = int(line.split()[0])
                    break
            subprocess.run(["wmctrl", "-s", str(current + 1)],
                           capture_output=True, text=True, timeout=5)
            _desktop_state["on_isolated"] = True
            return f"Switched to workspace {current + 1}."
        except FileNotFoundError:
            return "wmctrl not installed. Run: sudo apt install wmctrl"

    else:  # close
        return "Linux workspaces cannot be closed programmatically via wmctrl."


# ------------------------------------------------------------------
# Core function
# ------------------------------------------------------------------

def do_virtual_desktop(action: str) -> str:
    action = validate_desktop_action(action)
    plat = _get_platform()
    if plat == "windows":
        return _desktop_windows(action)
    elif plat == "darwin":
        return _desktop_darwin(action)
    return _desktop_linux(action)


# ------------------------------------------------------------------
# MCP tool registration
# ------------------------------------------------------------------

def register(server) -> int:
    """Register the *virtual_desktop* tool on *server*.

    Returns the number of tools registered (1).
    """

    @server.tool()
    def virtual_desktop(action: str) -> str:
        """Create an isolated virtual desktop so Claude can work without
        disturbing the user's windows.

        Use "create" before interactive GUI work, "close" when done.
        The user's desktop stays untouched on the original desktop.

        Parameters:
            action: One of "create" (new desktop), "switch_left" (go left),
                    "switch_right" (go right), "close" (close current desktop).
        """
        return do_virtual_desktop(action)

    return 1

