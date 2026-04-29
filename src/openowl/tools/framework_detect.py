"""UI framework detection for the foreground (or named) window.

Identifies the GUI toolkit in use and provides automation hints:
    qt, wpf, winforms, electron, java_swing, java_fx, gtk, win32, unknown

Detection uses four signals in priority order:
    1. Window class name prefix matching
    2. Known Win32 native class names
    3. Process executable name
    4. Loaded DLLs (only when steps 1-3 don't match)

Reuses _get_class_name, _get_process_name, _get_foreground_hwnd from
tools.window_classify.  Defines its own _get_hwnd_for_window (EnumWindows
title search) and _get_loaded_dlls (EnumProcessModulesEx).
"""

import platform
import sys
from typing import Optional

from openowl.tools.window_classify import _get_class_name, _get_process_name, _get_foreground_hwnd

# ---------------------------------------------------------------------------
# Detection data
# ---------------------------------------------------------------------------

# Window class prefix -> framework
_CLASS_PATTERNS: list[tuple[str, str]] = [
    ("Qt",                  "qt"),
    ("HwndWrapper",         "wpf"),
    ("WindowsForms",        "winforms"),
    ("Chrome_WidgetWin",    "electron"),  # disambiguated vs browsers below
    ("SunAwt",              "java_swing"),
    ("gdkWindowToplevel",   "gtk"),
    ("gdkWindowChild",      "gtk"),
    ("ApplicationFrameWindow", "uwp"),
    ("Windows.UI.Core.CoreWindow", "uwp"),
]

# Browser processes that use Chrome_WidgetWin but aren't Electron apps
_BROWSER_PROCESSES: set[str] = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe",
    "vivaldi.exe", "arc.exe",
}

# Known Win32 native window classes
_WIN32_CLASSES: set[str] = {
    "Notepad",
    "CabinetWClass",
    "ExploreWClass",
    "Shell_TrayWnd",
    "#32770",
}

# Process name -> framework
_PROCESS_FRAMEWORKS: dict[str, str] = {
    "electron.exe": "electron",
    "javaw.exe":    "java_swing",
    "java.exe":     "java_swing",
}

# DLL substring -> framework  (checked only when earlier steps fail)
_DLL_PATTERNS: list[tuple[str, str]] = [
    ("Qt6Core",             "qt"),
    ("Qt5Core",             "qt"),
    ("wpfgfx",              "wpf"),
    ("PresentationCore",    "wpf"),
    ("jvm.dll",             "java_swing"),
    ("libgtk",              "gtk"),
]

# Framework -> (uia_support, hints)
_FRAMEWORK_INFO: dict[str, tuple[str, list[str]]] = {
    "qt": (
        "partial",
        [
            "Qt app detected. Standard widgets (buttons, spinboxes, combos, tabs) work via accessibility.",
            "Custom QWidgets without QAccessibleInterface are invisible — use OCR fallback.",
            "QML/QtQuick content requires explicit Accessible{} declarations or it won't appear.",
        ],
    ),
    "wpf": (
        "full",
        [
            "WPF app detected. Full UIA support — all standard controls have AutomationPeers.",
            "Use AutomationId (x:Name) for the most reliable element selection.",
            "Virtualized lists may hide off-screen items; scroll to reveal them.",
        ],
    ),
    "winforms": (
        "partial",
        [
            "WinForms app detected. Standard controls are accessible.",
            "Owner-drawn controls and custom UserControls may be invisible.",
            "DataGridView cells may lack names on older .NET Framework versions.",
        ],
    ),
    "electron": (
        "conditional",
        [
            "Electron/Chromium app detected. Accessibility may be disabled by default.",
            "If elements aren't found, the app may need --force-renderer-accessibility flag.",
            "Canvas-rendered content (editors, graphs) requires OCR — no UIA elements exist.",
        ],
    ),
    "java_swing": (
        "conditional",
        [
            "Java Swing app detected. UIA does NOT work with Swing — it uses Java Access Bridge (JAB).",
            "Enable JAB with: jabswitch -enable (requires restart).",
            "Use OCR as primary fallback for Swing applications.",
        ],
    ),
    "java_fx": (
        "full",
        [
            "JavaFX app detected. Standard controls have native UIA support.",
            "Canvas-drawn nodes and WebView content are not accessible.",
        ],
    ),
    "gtk": (
        "none",
        [
            "GTK app detected. No UIA support on Windows — the ATK-to-UIA bridge is not functional.",
            "Use OCR and coordinate-based automation exclusively.",
            "screenshot(region) + find_text is the recommended workflow.",
        ],
    ),
    "win32": (
        "partial",
        [
            "Native Win32 app detected. Standard Common Controls are accessible.",
            "Owner-drawn buttons and custom-painted controls may lack names.",
            "Menus are only accessible while open.",
        ],
    ),
    "chromium_browser": (
        "conditional",
        [
            "Chromium-based browser detected (Chrome, Edge, Brave, etc.).",
            "Use accessibility tree for standard page elements; canvas/WebGL content needs OCR.",
            "Browser DevTools accessibility panel can help identify element roles.",
        ],
    ),
    "uwp": (
        "full",
        [
            "UWP/WinUI app detected. XAML controls have full UIA support.",
            "Use AutomationId for the most reliable element selection.",
            "Flyout menus and popups may appear as separate top-level windows.",
        ],
    ),
    "unknown": (
        "unknown",
        [
            "Unknown UI framework. Try find_element first; fall back to OCR if needed.",
        ],
    ),
}


# ---------------------------------------------------------------------------
# Win32 helpers
# ---------------------------------------------------------------------------

def _get_hwnd_for_window(window_title: Optional[str] = None) -> int:
    """Get window handle by title search, or foreground window if title is None.

    Uses EnumWindows to find the first visible window whose title contains
    the search string (case-insensitive).
    """
    if window_title is None:
        return _get_foreground_hwnd()

    if platform.system().lower() != "windows":
        return 0

    import ctypes
    import ctypes.wintypes

    found_hwnd = ctypes.wintypes.HWND(0)
    title_lower = window_title.lower()

    @ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def _enum_callback(hwnd, _lparam):
        nonlocal found_hwnd
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True  # continue
        buf = ctypes.create_unicode_buffer(512)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, 512)
        if title_lower in buf.value.lower():
            found_hwnd = hwnd
            return False  # stop enumeration
        return True

    ctypes.windll.user32.EnumWindows(_enum_callback, 0)
    return found_hwnd or 0


def _get_loaded_dlls(hwnd: int) -> list[str]:
    """Get loaded DLL file names for the process owning *hwnd*.

    Uses EnumProcessModulesEx + GetModuleFileNameExW.
    Returns a list of DLL basenames (e.g. ['Qt6Core.dll', 'kernel32.dll']).
    """
    if platform.system().lower() != "windows":
        return []

    import ctypes
    import ctypes.wintypes

    # Get PID from hwnd
    pid = ctypes.wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return []

    # Open process
    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010
    handle = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid.value
    )
    if not handle:
        return []

    try:
        psapi = ctypes.windll.psapi
        MAX_MODULES = 1024
        HMODULE = ctypes.c_void_p  # Must use c_void_p for 64-bit module handles
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
# Core detection
# ---------------------------------------------------------------------------

def do_detect_framework(window_title: Optional[str] = None) -> dict:
    """Detect the UI framework of a window.

    Args:
        window_title: Partial window title to search for.  If None, uses
                      the foreground window.

    Returns:
        dict with keys: framework, uia_support, hints, process_name, class_name
    """
    if sys.platform == "darwin":
        from openowl.owl_platform.darwin_backend import detect_framework_darwin
        return detect_framework_darwin(window_title)

    hwnd = _get_hwnd_for_window(window_title)
    if not hwnd:
        return {
            "framework": "unknown",
            "uia_support": "unknown",
            "hints": ["No window found to inspect."],
            "process_name": "",
            "class_name": "",
        }

    class_name = _get_class_name(hwnd)
    process_name = _get_process_name(hwnd)
    framework = None

    # 1. Window class prefix matching
    for prefix, fw in _CLASS_PATTERNS:
        if class_name.startswith(prefix):
            framework = fw
            break

    # 1b. Disambiguate Chromium browsers from Electron apps
    if framework == "electron" and process_name.lower() in _BROWSER_PROCESSES:
        framework = "chromium_browser"

    # 2. Known Win32 native classes
    if not framework and class_name in _WIN32_CLASSES:
        framework = "win32"

    # 3. Process name
    if not framework:
        proc_lower = process_name.lower()
        for proc, fw in _PROCESS_FRAMEWORKS.items():
            if proc_lower == proc.lower():
                framework = fw
                break

    # 4. Loaded DLLs (only when earlier steps fail)
    if not framework:
        dlls = _get_loaded_dlls(hwnd)
        dll_names_lower = [d.lower() for d in dlls]
        for pattern, fw in _DLL_PATTERNS:
            pattern_lower = pattern.lower()
            if any(pattern_lower in d for d in dll_names_lower):
                framework = fw
                break
    else:
        dlls = []

    if not framework:
        framework = "unknown"

    uia_support, hints = _FRAMEWORK_INFO.get(
        framework, ("unknown", ["Framework not recognized."])
    )

    return {
        "framework": framework,
        "uia_support": uia_support,
        "hints": hints,
        "process_name": process_name,
        "class_name": class_name,
    }


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------

def register(server) -> int:
    """Register the detect_framework MCP tool."""

    @server.tool()
    def detect_framework(window_title: str = "") -> str:
        """Detect the UI framework of a window and get automation hints.

        Identifies the GUI toolkit (Qt, WPF, WinForms, Electron, Java Swing,
        GTK, Win32) and reports UIA support level with actionable hints.

        Parameters:
            window_title: Partial title of the target window (default: foreground).
        """
        result = do_detect_framework(window_title or None)
        lines = [
            f"Framework: {result['framework']}",
            f"UIA support: {result['uia_support']}",
            f"Process: {result['process_name']}",
            f"Class: {result['class_name']}",
            "",
            "Hints:",
        ]
        for hint in result["hints"]:
            lines.append(f"  - {hint}")
        return "\n".join(lines)

    return 1

