"""Target window auto-focus -- session state for multi-window workflows.

When a target window is set, every input action (click, type, keys, scroll,
drag, hover) will auto-focus that window before executing.  This eliminates
the need to call ``focus_window`` before every interaction when the terminal
keeps stealing focus between tool calls.

Provides two MCP tools:
    set_target_window  - set (or clear) the target window title
    get_target_window  - return the current target window title
"""

import sys
from typing import Optional

_target_window: Optional[str] = None


def _find_ancestor_window() -> bool:
    """Walk up the process tree and focus the first ancestor with a window.

    The MCP server is a child of the host terminal (e.g. Claude Code).
    By walking up PIDs we can find and focus the terminal without
    guessing window titles.  Returns True if successful.
    """
    if sys.platform == "win32":
        return _find_ancestor_window_win32()
    elif sys.platform == "darwin":
        return _find_ancestor_window_darwin()
    return False


def _find_ancestor_window_win32() -> bool:
    """Win32: walk parent PIDs, find one that owns a visible window."""
    try:
        import ctypes
        import ctypes.wintypes
        import os

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # Collect visible windows → PID mapping
        GetWindowThreadProcessId = user32.GetWindowThreadProcessId
        IsWindowVisible = user32.IsWindowVisible
        GetWindowTextLengthW = user32.GetWindowTextLengthW
        EnumWindows = user32.EnumWindows
        EnumWindowsProc = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
        )

        pid_to_hwnd: dict[int, int] = {}

        def _enum(hwnd, _lp):
            if IsWindowVisible(hwnd) and GetWindowTextLengthW(hwnd) > 0:
                pid = ctypes.wintypes.DWORD()
                GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                # Keep first (topmost) window per PID
                if pid.value not in pid_to_hwnd:
                    pid_to_hwnd[pid.value] = hwnd
            return True

        EnumWindows(EnumWindowsProc(_enum), 0)

        # Walk up parent chain (max 10 levels to avoid infinite loops)
        pid = os.getpid()
        for _ in range(10):
            pid = _get_parent_pid_win32(pid)
            if pid is None or pid <= 0:
                break
            if pid in pid_to_hwnd:
                hwnd = pid_to_hwnd[pid]
                # Focus this window
                from openowl.tools.windows import do_focus_window
                SetForegroundWindow = user32.SetForegroundWindow
                ShowWindow = user32.ShowWindow
                BringWindowToTop = user32.BringWindowToTop
                GetForegroundWindow = user32.GetForegroundWindow
                AttachThreadInput = user32.AttachThreadInput
                GetCurrentThreadId = kernel32.GetCurrentThreadId

                fg_hwnd = GetForegroundWindow()
                fg_tid = GetWindowThreadProcessId(fg_hwnd, None)
                our_tid = GetCurrentThreadId()

                if fg_tid != our_tid:
                    AttachThreadInput(our_tid, fg_tid, True)

                ShowWindow(hwnd, 9)  # SW_RESTORE
                BringWindowToTop(hwnd)
                SetForegroundWindow(hwnd)

                if fg_tid != our_tid:
                    AttachThreadInput(our_tid, fg_tid, False)

                import time
                for _ in range(50):
                    time.sleep(0.01)
                    if GetForegroundWindow() == hwnd:
                        return True
                return True  # best effort even if poll didn't confirm
    except Exception:
        pass
    return False


def _get_parent_pid_win32(pid: int):
    """Get parent PID using CreateToolhelp32Snapshot."""
    try:
        import ctypes
        import ctypes.wintypes

        TH32CS_SNAPPROCESS = 0x00000002

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", ctypes.wintypes.DWORD),
                ("cntUsage", ctypes.wintypes.DWORD),
                ("th32ProcessID", ctypes.wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", ctypes.wintypes.DWORD),
                ("cntThreads", ctypes.wintypes.DWORD),
                ("th32ParentProcessID", ctypes.wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", ctypes.wintypes.DWORD),
                ("szExeFile", ctypes.c_char * 260),
            ]

        kernel32 = ctypes.windll.kernel32
        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot == -1:
            return None

        try:
            entry = PROCESSENTRY32()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
            if not kernel32.Process32First(snapshot, ctypes.byref(entry)):
                return None
            while True:
                if entry.th32ProcessID == pid:
                    return entry.th32ParentProcessID
                if not kernel32.Process32Next(snapshot, ctypes.byref(entry)):
                    return None
        finally:
            kernel32.CloseHandle(snapshot)
    except Exception:
        return None


def _find_ancestor_window_darwin() -> bool:
    """macOS: walk parent PIDs, match against window-owning processes."""
    try:
        import os
        import subprocess

        pid = os.getpid()
        for _ in range(10):
            # Get parent PID
            result = subprocess.run(
                ["ps", "-o", "ppid=", "-p", str(pid)],
                capture_output=True, text=True, timeout=2,
            )
            ppid_str = result.stdout.strip()
            if not ppid_str:
                break
            pid = int(ppid_str)
            if pid <= 1:
                break

            # Get process name
            result = subprocess.run(
                ["ps", "-o", "comm=", "-p", str(pid)],
                capture_output=True, text=True, timeout=2,
            )
            comm = result.stdout.strip()
            if not comm:
                continue

            # Try to activate this app via AppleScript
            app_name = os.path.basename(comm)
            activate = subprocess.run(
                ["osascript", "-e",
                 f'tell application "System Events" to set frontmost '
                 f'of (first process whose unix id is {pid}) to true'],
                capture_output=True, timeout=3,
            )
            if activate.returncode == 0:
                return True
    except Exception:
        pass
    return False


def _refocus_host_terminal() -> None:
    """Bring the host terminal back to the foreground.

    Primary strategy: walk the process tree to find the ancestor that
    owns a visible window (works regardless of terminal app name).
    Fallback: try common terminal window titles.
    """
    # Strategy 1: process-tree walk (reliable, no title guessing)
    if _find_ancestor_window():
        return

    # Strategy 2: title-based fallback
    try:
        from openowl.tools.windows import do_focus_window
        if sys.platform == "darwin":
            for name in ("Terminal", "iTerm2", "Ghostty", "Alacritty", "kitty", "Warp"):
                result = do_focus_window(name, action="focus")
                if result.get("success"):
                    return
        else:
            import os
            title = os.environ.get("CLAUDE_TERMINAL_TITLE", "")
            if title:
                result = do_focus_window(title, action="focus")
                if result.get("success"):
                    return
            for name in ("Claude Code", "Windows Terminal", "PowerShell",
                         "Command Prompt", "cmd.exe"):
                result = do_focus_window(name, action="focus")
                if result.get("success"):
                    return
    except Exception:
        pass


def set_target(title: Optional[str]) -> None:
    """Set (or clear) the target window for auto-focus."""
    global _target_window
    was_set = _target_window is not None
    if title is not None and title.strip() == "":
        title = None
    _target_window = title
    if title:
        print(f"[target_window] Target set: {title!r}", file=sys.stderr)
    else:
        print("[target_window] Target cleared", file=sys.stderr)
        if was_set:
            _refocus_host_terminal()


def get_target() -> Optional[str]:
    """Return the current target window title, or ``None``."""
    return _target_window


def ensure_focus() -> None:
    """If a target window is set, focus it before an input action."""
    if _target_window is None:
        return
    from openowl.tools.windows import do_focus_window
    do_focus_window(_target_window, action="focus")


# ------------------------------------------------------------------
# MCP tool registration
# ------------------------------------------------------------------

def register(server) -> int:
    """Register the target-window tools on *server*. Returns 2."""

    @server.tool()
    def set_target_window(title: str = "") -> str:
        """Set or clear the target window for automatic focus.

        When set, every input action (click, type_text, send_keys, scroll,
        drag, hover) will auto-focus this window before executing.  This
        prevents the terminal from stealing focus between tool calls.

        When cleared (empty string), the host terminal is automatically
        brought back to the foreground.

        IMPORTANT: You MUST clear the target (pass empty string) when you
        are done interacting with a GUI application. This brings the
        terminal back to the foreground so the user can see your output.
        Without this, the user has no way to know you are finished.

        Parameters:
            title: Partial window title to match (case-insensitive).
                   Pass empty string to clear the target.
        """
        set_target(title if title else None)
        current = get_target()
        if current:
            ensure_focus()
            return f"Target window set to {current!r}. All input actions will auto-focus this window. REMEMBER: call set_target_window('') when done to return focus to the terminal."
        return "Target window cleared. Terminal refocused."

    @server.tool()
    def get_target_window() -> str:
        """Get the current target window for automatic focus.

        Returns the partial title being used for auto-focus, or a message
        indicating no target is set.
        """
        current = get_target()
        if current:
            return f"Target window: {current!r}"
        return "No target window set."

    return 2

