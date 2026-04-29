"""Win32 platform backend for openowl.

Implements platform-specific functions for Windows using ctypes and Win32 APIs.
All functions are Windows-only -- they are gated by openowl.owl_platform.__init__.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import sys

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
]

# ---------------------------------------------------------------------------
# Virtual key code mapping for console key injection
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# DPI
# ---------------------------------------------------------------------------

def get_dpi_scale() -> float:
    """Return the DPI scale factor for the primary monitor (e.g. 1.5 for 150%)."""
    try:
        # SetProcessDPIAware so GetDpiForSystem returns the actual DPI
        ctypes.windll.user32.SetProcessDPIAware()
        dpi = ctypes.windll.user32.GetDpiForSystem()
        return dpi / 96.0
    except Exception:
        return 1.0


# ---------------------------------------------------------------------------
# Foreground window
# ---------------------------------------------------------------------------

def get_foreground_title() -> str:
    """Return the title of the current foreground window."""
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def get_foreground_hwnd() -> int:
    """Return the HWND of the current foreground window."""
    return ctypes.windll.user32.GetForegroundWindow()


# ---------------------------------------------------------------------------
# Process helpers
# ---------------------------------------------------------------------------

def get_process_name_for_pid(pid: int) -> str:
    """Return the executable filename (e.g. 'chrome.exe') for a given PID."""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid
    )
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = ctypes.wintypes.DWORD(260)
        if ctypes.windll.kernel32.QueryFullProcessImageNameW(
            handle, 0, buf, ctypes.byref(size)
        ):
            path = buf.value
            return path.rsplit("\\", 1)[-1] if "\\" in path else path
        return ""
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def is_elevated(pid: int = None) -> bool:
    """Return True if the given process (or current process) is running elevated (admin)."""
    if pid is None:
        pid = os.getpid()
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid
    )
    if not handle:
        return False  # Can't open likely means the process is elevated and we're not
    try:
        token = ctypes.wintypes.HANDLE()
        TOKEN_QUERY = 0x0008
        if not ctypes.windll.advapi32.OpenProcessToken(
            handle, TOKEN_QUERY, ctypes.byref(token)
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


# ---------------------------------------------------------------------------
# Window class name
# ---------------------------------------------------------------------------

def get_class_name(hwnd) -> str:
    """Return the window class name for a given HWND."""
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


# ---------------------------------------------------------------------------
# List windows
# ---------------------------------------------------------------------------

def list_windows_native() -> list[dict]:
    """List visible windows using Win32 EnumWindows via ctypes.

    Returns a list of dicts with keys:
        title, process_name, pid, x, y, width, height
    """
    user32 = ctypes.windll.user32

    EnumWindowsProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
    )

    windows = []

    def enum_callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if not title:
            return True

        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))

        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc_name = get_process_name_for_pid(pid.value)

        windows.append({
            "title": title,
            "process_name": proc_name,
            "pid": pid.value,
            "x": rect.left,
            "y": rect.top,
            "width": rect.right - rect.left,
            "height": rect.bottom - rect.top,
        })
        return True

    user32.EnumWindows(EnumWindowsProc(enum_callback), 0)
    return windows


# ---------------------------------------------------------------------------
# Focus window
# ---------------------------------------------------------------------------

def focus_window_native(title: str, action: str) -> dict:
    """Focus, minimize, maximize, or restore a window on Windows.

    Parameters
    ----------
    title : str
        Partial window title or process name to match (case-insensitive).
    action : str
        One of ``focus``, ``minimize``, ``maximize``, ``restore``.
    """
    user32 = ctypes.windll.user32

    EnumWindowsProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
    )

    SW_MINIMIZE = 6
    SW_MAXIMIZE = 3
    SW_RESTORE = 9
    SW_SHOW = 5

    all_hwnds = []  # list of (hwnd, {"title": ..., "process_name": ...})

    def enum_callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        win_title = buf.value
        if not win_title:
            return True

        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc_name = get_process_name_for_pid(pid.value)

        all_hwnds.append((hwnd, {"title": win_title, "process_name": proc_name}))
        return True

    user32.EnumWindows(EnumWindowsProc(enum_callback), 0)

    # Match by title substring, then process name
    title_lower = title.lower()
    target_hwnd = None
    target_title = None

    for hwnd, info in all_hwnds:
        if title_lower in info["title"].lower():
            target_hwnd = hwnd
            target_title = info["title"]
            break

    if target_hwnd is None:
        for hwnd, info in all_hwnds:
            proc = info.get("process_name", "")
            if proc:
                stem = proc.rsplit(".", 1)[0].lower()
                if title_lower == stem or title_lower in stem:
                    target_hwnd = hwnd
                    target_title = info["title"]
                    break

    if target_hwnd is None:
        return {"success": False, "error": f"No window matching '{title}' found"}

    if action == "focus":
        GetForegroundWindow = user32.GetForegroundWindow
        GetWindowThreadProcessId = user32.GetWindowThreadProcessId
        GetCurrentThreadId = ctypes.windll.kernel32.GetCurrentThreadId
        AttachThreadInput = user32.AttachThreadInput
        BringWindowToTop = user32.BringWindowToTop
        SetForegroundWindow = user32.SetForegroundWindow

        fg_hwnd = GetForegroundWindow()
        fg_tid = GetWindowThreadProcessId(fg_hwnd, None)
        our_tid = GetCurrentThreadId()

        if fg_tid != our_tid:
            AttachThreadInput(our_tid, fg_tid, True)

        user32.ShowWindow(target_hwnd, SW_RESTORE)
        BringWindowToTop(target_hwnd)
        SetForegroundWindow(target_hwnd)

        if fg_tid != our_tid:
            AttachThreadInput(our_tid, fg_tid, False)

        import time
        for _ in range(50):
            time.sleep(0.01)
            if GetForegroundWindow() == target_hwnd:
                break

    elif action == "minimize":
        user32.ShowWindow(target_hwnd, SW_MINIMIZE)
    elif action == "maximize":
        user32.ShowWindow(target_hwnd, SW_MAXIMIZE)
    elif action == "restore":
        user32.ShowWindow(target_hwnd, SW_RESTORE)
    else:
        return {"success": False, "error": f"Unknown action: {action}"}

    return {"success": True, "window": target_title, "action": action}


# ---------------------------------------------------------------------------
# Window classification
# ---------------------------------------------------------------------------

_CONSOLE_CLASSES = {"ConsoleWindowClass"}
_TERMINAL_PROCESSES = {"WindowsTerminal.exe", "wt.exe"}
_BROWSER_PROCESSES = {"msedge.exe", "chrome.exe", "firefox.exe", "brave.exe", "opera.exe"}
_ELECTRON_PROCESSES = {"Code.exe", "Slack.exe", "Discord.exe", "Obsidian.exe",
                       "Notion.exe", "Postman.exe", "GitHubDesktop.exe"}


def classify_window_native(handle=None) -> dict:
    """Classify the foreground (or given) window on Windows.

    Returns dict with: type, process_name, class_name, pid, is_elevated
    """
    if handle is None:
        hwnd = get_foreground_hwnd()
    else:
        hwnd = handle

    class_name = get_class_name(hwnd)

    pid = ctypes.wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    pid_val = pid.value

    process_name = get_process_name_for_pid(pid_val) if pid_val else ""

    if class_name in _CONSOLE_CLASSES:
        win_type = "console"
    elif process_name in _TERMINAL_PROCESSES:
        win_type = "terminal"
    elif process_name in _BROWSER_PROCESSES:
        win_type = "browser"
    elif process_name in _ELECTRON_PROCESSES:
        win_type = "electron"
    else:
        win_type = "generic"

    return {
        "type": win_type,
        "process_name": process_name,
        "class_name": class_name,
        "pid": pid_val,
        "is_elevated": is_elevated(pid_val) if pid_val else False,
    }


# ---------------------------------------------------------------------------
# OCR (Windows.Media.Ocr via winsdk -- optional)
# ---------------------------------------------------------------------------

def run_ocr_native(image_path: str) -> list[dict]:
    """Run OCR on an image using Windows.Media.Ocr (WinRT).

    Requires the 'winsdk' package. Returns an empty list if unavailable --
    the caller (ocr.py) will fall back to RapidOCR in that case.
    """
    try:
        import asyncio
        import winsdk.windows.media.ocr as wocr
        import winsdk.windows.graphics.imaging as wgi
        import winsdk.windows.storage as wstorage
        import winsdk.windows.storage.streams as wstreams
    except ImportError:
        return []

    from PIL import Image as PILImage

    try:
        with PILImage.open(image_path) as img:
            img_width, img_height = img.size
    except Exception:
        return []

    async def _do_ocr():
        abs_path = os.path.abspath(image_path).replace("/", "\\")
        try:
            file = await wstorage.StorageFile.get_file_from_path_async(abs_path)
            stream = await file.open_async(wstorage.FileAccessMode.READ)
            decoder = await wgi.BitmapDecoder.create_async(stream)
            bitmap = await decoder.get_software_bitmap_async()
        except Exception:
            return []

        engine = wocr.OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            return []

        result = await engine.recognize_async(bitmap)
        if result is None:
            return []

        words = []
        for line in result.lines:
            for word in line.words:
                bbox = word.bounding_rect
                words.append({
                    "text": word.text,
                    "x": int(bbox.x),
                    "y": int(bbox.y),
                    "width": int(bbox.width),
                    "height": int(bbox.height),
                })
        return words

    try:
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(_do_ocr())
        loop.close()
        return result
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Console input (WriteConsoleInputW)
# ---------------------------------------------------------------------------

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


def send_text_to_console(pid, text, hwnd=0) -> dict:
    """Send text to a console process via WriteConsoleInputW.

    Falls back to SendMessage(WM_CHAR) if AttachConsole fails (e.g., elevated console).
    """
    WM_CHAR = 0x0102
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32

    kernel32.FreeConsole()
    if kernel32.AttachConsole(pid):
        try:
            handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
            written = ctypes.wintypes.DWORD()
            for ch in text:
                rec_down = _make_key_event(ord(ch), is_char=True, key_down=True)
                kernel32.WriteConsoleInputW(handle, ctypes.byref(rec_down), 1, ctypes.byref(written))
                rec_up = _make_key_event(ord(ch), is_char=True, key_down=False)
                kernel32.WriteConsoleInputW(handle, ctypes.byref(rec_up), 1, ctypes.byref(written))
            return {"success": True, "method": "WriteConsoleInput", "chars": len(text)}
        finally:
            kernel32.FreeConsole()
    else:
        if not hwnd:
            return {"success": False, "error": "AttachConsole failed and no hwnd for SendMessage"}
        for ch in text:
            user32.SendMessageW(hwnd, WM_CHAR, ord(ch), 0)
        return {"success": True, "method": "SendMessage", "chars": len(text)}


def send_keys_to_console(pid, keys, hwnd=0) -> dict:
    """Send special keys (enter, tab, arrows, etc.) to a console process."""
    parts = [k.strip().lower() for k in keys.split("+")]
    kernel32 = ctypes.windll.kernel32

    kernel32.FreeConsole()
    if kernel32.AttachConsole(pid):
        try:
            handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
            written = ctypes.wintypes.DWORD()
            for key in parts:
                vk = _VK_MAP.get(key)
                if vk:
                    rec_down = _make_key_event(vk, is_char=False, key_down=True)
                    kernel32.WriteConsoleInputW(handle, ctypes.byref(rec_down), 1, ctypes.byref(written))
                    rec_up = _make_key_event(vk, is_char=False, key_down=False)
                    kernel32.WriteConsoleInputW(handle, ctypes.byref(rec_up), 1, ctypes.byref(written))
                elif len(key) == 1:
                    rec = _make_key_event(ord(key), is_char=True, key_down=True)
                    kernel32.WriteConsoleInputW(handle, ctypes.byref(rec), 1, ctypes.byref(written))
            return {"success": True, "method": "WriteConsoleInput"}
        finally:
            kernel32.FreeConsole()
    else:
        return {"success": False, "error": "AttachConsole failed"}


# ---------------------------------------------------------------------------
# Loaded modules (DLLs)
# ---------------------------------------------------------------------------

def get_loaded_modules(pid) -> list[str]:
    """Get loaded DLL file names for a process by PID."""
    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010
    handle = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid
    )
    if not handle:
        return []

    try:
        psapi = ctypes.windll.psapi
        MAX_MODULES = 1024
        HMODULE = ctypes.c_void_p
        modules = (HMODULE * MAX_MODULES)()
        needed = ctypes.wintypes.DWORD()

        # LIST_MODULES_ALL = 0x03
        if not psapi.EnumProcessModulesEx(
            handle, ctypes.byref(modules), ctypes.sizeof(modules),
            ctypes.byref(needed), 0x03
        ):
            return []

        count = min(needed.value // ctypes.sizeof(HMODULE), MAX_MODULES)
        dlls = []
        buf = ctypes.create_unicode_buffer(260)
        for i in range(count):
            mod = modules[i]
            if mod and psapi.GetModuleFileNameExW(handle, HMODULE(mod), buf, 260):
                path = buf.value
                basename = path.rsplit("\\", 1)[-1] if "\\" in path else path
                dlls.append(basename)
        return dlls
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


# ---------------------------------------------------------------------------
# Host terminal HWND
# ---------------------------------------------------------------------------

def find_host_terminal_hwnd() -> int | None:
    """Walk up the process tree to find the terminal window HWND.

    Returns the HWND of the terminal that launched this process, or None.
    """
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

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

    # Walk up from current PID
    chain = []
    current = os.getpid()
    for _ in range(20):
        chain.append(current)
        parent = pid_to_parent.get(current)
        if parent is None or parent == 0 or parent == current:
            break
        current = parent

    # Find first ancestor PID with a visible window
    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
    )

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
                return False
            return True

        user32.EnumWindows(WNDENUMPROC(_callback), 0)
        if found_hwnd[0]:
            return int(found_hwnd[0])

    return None
