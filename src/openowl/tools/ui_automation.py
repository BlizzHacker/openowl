"""UI Automation tools -- find and click elements by name/role.

Uses pywinauto's UIA backend on Windows and AXUIElement on macOS to access
the accessibility tree.  Elements are identified by name (text label) and
role (control type).  Coordinates are always screen-absolute and DPI-aware.

Provides three MCP tools:
    find_element   - find UI element by name/role in a window
    click_element  - find + click center of element
    list_elements  - dump accessible element tree for a window
"""
from __future__ import annotations

import hashlib
import sys
from difflib import SequenceMatcher
from typing import Optional

from openowl.tools.input_tools import do_click


def _get_desktop():
    """Get pywinauto Desktop instance (lazy import)."""
    from pywinauto import Desktop
    return Desktop(backend="uia")


def _find_window(desktop, window_title: str):
    """Find the first window matching partial title using shared matching."""
    from openowl.tools.windows import find_matching_window
    windows = []
    for win in desktop.windows():
        try:
            windows.append({"_obj": win, "title": win.window_text()})
        except Exception:
            continue
    result = find_matching_window(window_title, windows)
    return result["window"]["_obj"] if result["window"] else None


def _element_to_dict(elem) -> dict:
    """Convert a pywinauto element to a serializable dict."""
    try:
        rect = elem.element_info.rectangle
        return {
            "name": elem.element_info.name or "",
            "role": elem.element_info.control_type or "",
            "x": rect.left,
            "y": rect.top,
            "width": rect.right - rect.left,
            "height": rect.bottom - rect.top,
            "value": getattr(elem.element_info, "rich_text", "") or "",
        }
    except Exception:
        return None


def _do_find_element_darwin(
    name: Optional[str] = None,
    role: Optional[str] = None,
    window_title: Optional[str] = None,
) -> dict:
    """macOS implementation using AXUIElement."""
    from openowl.owl_platform.darwin_backend import (
        ax_get_frontmost_app,
        ax_get_app_for_title,
        ax_find_elements,
    )

    if window_title:
        root = ax_get_app_for_title(window_title)
        if root is None:
            return {"found": False, "elements": [], "error": f"Window '{window_title}' not found"}
    else:
        root = ax_get_frontmost_app()
        if root is None:
            return {"found": False, "elements": [], "error": "No frontmost application found"}

    matches = ax_find_elements(root, name=name, role=role)
    if not matches:
        return {"found": False, "elements": []}
    return {"found": True, "elements": matches}


def _do_list_elements_darwin(
    window_title: Optional[str] = None,
    max_depth: int = 5,
    role: Optional[str] = None,
) -> dict:
    """macOS implementation using AXUIElement."""
    from openowl.owl_platform.darwin_backend import (
        ax_get_frontmost_app,
        ax_get_app_for_title,
        ax_find_elements,
    )

    if window_title:
        root = ax_get_app_for_title(window_title)
        if root is None:
            return {"elements": [], "error": f"Window '{window_title}' not found"}
    else:
        root = ax_get_frontmost_app()
        if root is None:
            return {"elements": [], "error": "No frontmost application found"}

    depth = 100 if role else max_depth  # walk full tree when filtering by role
    all_elements = ax_find_elements(root, role=role, max_depth=depth)
    return {"elements": all_elements, "count": len(all_elements)}


def do_find_element(
    name: Optional[str] = None,
    role: Optional[str] = None,
    window_title: Optional[str] = None,
    index: int = 0,
) -> dict:
    """Find UI elements by name and/or role in a window.

    Returns dict with 'found' bool and 'elements' list.
    """
    if sys.platform == "darwin":
        return _do_find_element_darwin(name, role, window_title)

    desktop = _get_desktop()

    if window_title:
        window = _find_window(desktop, window_title)
        if not window:
            return {"found": False, "elements": [], "error": f"Window '{window_title}' not found"}
    else:
        # Use foreground window
        windows = desktop.windows()
        window = windows[0] if windows else None
        if not window:
            return {"found": False, "elements": [], "error": "No windows found"}

    try:
        descendants = window.descendants()
    except Exception as e:
        return {"found": False, "elements": [], "error": str(e)}

    matches = []
    for elem in descendants:
        try:
            info = elem.element_info
            name_match = (name is None) or (name.lower() in (info.name or "").lower())
            role_match = (role is None) or (role.lower() == (info.control_type or "").lower())
            if name_match and role_match:
                d = _element_to_dict(elem)
                if d:
                    matches.append(d)
        except Exception:
            continue

    if not matches:
        return {"found": False, "elements": []}

    return {"found": True, "elements": matches}


def _fuzzy_find_nearest(
    name: Optional[str],
    role: Optional[str],
    window_title: Optional[str],
) -> Optional[dict]:
    """Find the nearest element by fuzzy name match or role proximity.

    Returns dict with 'element', 'confidence', 'match_method' or None.
    Adapted from Nubaeon/empirica-iris matcher.py.
    """
    all_result = do_list_elements(window_title=window_title, role=role)
    candidates = all_result.get("elements", [])
    if not candidates:
        return None

    best = None
    best_confidence = 0.0
    best_method = "none"

    for elem in candidates:
        confidence = 0.0
        method = "role_only"

        if name and elem.get("name"):
            ratio = SequenceMatcher(None, name.lower(), elem["name"].lower()).ratio()
            if ratio >= 0.6:
                confidence = 0.5 + (ratio * 0.4)  # 0.6-1.0 ratio -> 0.74-0.9 confidence
                method = "fuzzy_name"

        if confidence == 0.0 and role:
            confidence = 0.4
            method = "role_only"

        if confidence > best_confidence:
            best_confidence = confidence
            best = elem
            best_method = method

    if best is None:
        return None

    return {
        "element": best,
        "confidence": round(best_confidence, 2),
        "match_method": best_method,
    }


def _click_and_report(elem: dict, method: str, name: str = "",
                      fallback_note: str = "") -> dict:
    """Click an element's center and report what's focused afterward.

    Shared by all click_element code paths (accessibility, fuzzy, OCR).
    """
    center_x = elem["x"] + elem["width"] // 2
    center_y = elem["y"] + elem["height"] // 2
    click_result = do_click(center_x, center_y)

    out = {
        "success": True,
        "element": elem,
        "clicked_at": {"x": center_x, "y": center_y},
        "method": method,
    }
    if fallback_note:
        out["fallback"] = True
        out["note"] = fallback_note
    if "navigation_warning" in click_result:
        out["navigation_warning"] = click_result["navigation_warning"]
    if "focused_element" in click_result:
        out["focused_element"] = click_result["focused_element"]
    return out


def do_click_element(
    name: Optional[str] = None,
    role: Optional[str] = None,
    window_title: Optional[str] = None,
    index: int = 0,
) -> dict:
    """Find an element and click its center. Uses a 3-tier cascade:

    1. Accessibility tree (exact match) — best for native apps
    2. Accessibility tree (fuzzy match, confidence >= 0.7)
    3. OCR text search — works for web apps where accessibility fails

    Always reports the focused element after clicking so the caller
    can verify the right field received focus.
    """
    # --- Tier 1: Accessibility exact match ---
    result = do_find_element(name=name, role=role, window_title=window_title)
    if result["found"]:
        idx = min(index, len(result["elements"]) - 1)
        return _click_and_report(result["elements"][idx], method="accessibility")

    # --- Tier 2: Accessibility fuzzy match ---
    nearest = _fuzzy_find_nearest(name=name, role=role, window_title=window_title)
    if nearest and nearest["confidence"] >= 0.7:
        elem = nearest["element"]
        return _click_and_report(
            elem, method="fuzzy_accessibility",
            fallback_note=(
                f"Exact match for '{name}' not found. Clicked nearest: "
                f"'{elem['name']}' ({nearest['match_method']}, confidence={nearest['confidence']})"
            ),
        )

    # --- Tier 3: OCR fallback — finds text on screen regardless of accessibility ---
    if name:
        try:
            from openowl.tools.ocr import do_click_text
            ocr_result = do_click_text(
                query=name,
                window_title=window_title,
            )
            if ocr_result.get("success"):
                m = ocr_result["match"]
                ocr_elem = {
                    "name": m["text"], "role": "text (OCR)",
                    "x": m["x"], "y": m["y"],
                    "width": m["width"], "height": m["height"],
                    "value": "",
                }
                out = {
                    "success": True,
                    "element": ocr_elem,
                    "clicked_at": ocr_result["clicked_at"],
                    "method": "ocr",
                    "fallback": True,
                    "note": f"Accessibility tree couldn't find '{name}'. Found and clicked via OCR.",
                }
                # Query focused element after OCR click
                try:
                    focus = do_get_focused_element()
                    if focus.get("found") and focus.get("element"):
                        e = focus["element"]
                        out["focused_element"] = {
                            "role": e.get("role", ""),
                            "name": e.get("name", ""),
                            "value": e.get("value", ""),
                        }
                except Exception:
                    pass
                return out
        except Exception:
            pass

    # --- All tiers failed ---
    error = f"Element '{name}' not found via accessibility or OCR."
    if nearest:
        elem = nearest["element"]
        error += (
            f" Nearest accessibility match: '{elem['name']}' "
            f"(confidence={nearest['confidence']}, too low to auto-click)."
        )
    return {"success": False, "error": error}


def do_list_elements(
    window_title: Optional[str] = None,
    max_depth: int = 5,
    role: Optional[str] = None,
) -> dict:
    """List accessible elements in a window.

    When role is set, walks the full tree (ignores max_depth) and
    returns only elements matching that control type.
    """
    if sys.platform == "darwin":
        return _do_list_elements_darwin(window_title, max_depth, role)

    desktop = _get_desktop()

    if window_title:
        window = _find_window(desktop, window_title)
        if not window:
            return {"elements": [], "error": f"Window '{window_title}' not found"}
    else:
        windows = desktop.windows()
        window = windows[0] if windows else None
        if not window:
            return {"elements": [], "error": "No windows found"}

    try:
        if role:
            descendants = window.descendants()  # Full tree walk
        else:
            descendants = window.descendants(depth=max_depth)
    except Exception as e:
        return {"elements": [], "error": str(e)}

    role_lower = role.lower() if role else None
    elements = []
    for elem in descendants:
        d = _element_to_dict(elem)
        if d and (d["name"] or d["role"]):
            if role_lower and (d["role"] or "").lower() != role_lower:
                continue
            elements.append(d)

    return {"elements": elements, "count": len(elements)}


def do_get_focused_element() -> dict:
    """Return the currently focused UI element.

    Uses IUIAutomation::GetFocusedElement via pywinauto (Windows) or
    AXFocusedUIElement via ApplicationServices (macOS).
    Returns dict with 'found' bool and 'element' dict.
    """
    if sys.platform == "darwin":
        from openowl.owl_platform.darwin_backend import ax_get_focused_element
        return ax_get_focused_element()

    try:
        desktop = _get_desktop()
        focused = desktop.get_focus()
        elem = _element_to_dict(focused)
        if elem:
            return {"found": True, "element": elem}
        return {"found": False, "error": "Focused element has no accessible info"}
    except Exception as e:
        return {"found": False, "error": str(e)}


def do_smart_find(
    name: str,
    role: Optional[str] = None,
    window_title: Optional[str] = None,
    index: int = 0,
) -> dict:
    """Resilient element discovery: UIA first, then OCR fallback.

    Returns dict with 'found', 'method' ('uia' or 'ocr'), and 'elements'.
    """
    # 1. Try UIA
    try:
        uia_result = do_find_element(name=name, role=role, window_title=window_title, index=index)
        if uia_result.get("found") and uia_result["elements"]:
            return {"found": True, "method": "uia", "elements": uia_result["elements"]}
    except Exception:
        pass  # UIA failed — fall through to OCR

    # 2. Fall back to OCR
    try:
        from openowl.tools.ocr import do_find_text
        ocr_result = do_find_text(name, window_title=window_title)
        if ocr_result["matches"]:
            # Normalize OCR matches to element-like dicts
            elements = []
            for m in ocr_result["matches"]:
                elements.append({
                    "name": m["text"],
                    "role": "text",
                    "x": m["x"],
                    "y": m["y"],
                    "width": m["width"],
                    "height": m["height"],
                    "value": "",
                })
            return {"found": True, "method": "ocr", "elements": elements}
    except Exception:
        pass

    # Get framework hint for the error message
    hint = ""
    try:
        from openowl.tools.framework_detect import do_detect_framework
        fw = do_detect_framework(window_title)
        if fw["framework"] != "unknown":
            hint = f" [{fw['framework'].upper()}: {fw['hints'][0]}]"
    except Exception:
        pass

    return {
        "found": False,
        "elements": [],
        "error": f"'{name}' not found via accessibility tree or OCR. Try click_text(query=\"{name}\") for direct OCR click, or use keyboard navigation (Tab/Enter).{hint}",
    }


def do_ui_fingerprint(
    window_title: Optional[str] = None,
    max_elements: int = 20,
) -> dict:
    """Compute a lightweight fingerprint of the current UI layout.

    Hashes the top N elements' roles, names, and quantized positions.
    Use to detect layout changes (modals, tab switches) without full re-query.
    Adapted from Nubaeon/empirica-iris staleness.py.
    """
    result = do_list_elements(window_title=window_title, max_depth=3)
    elements = result.get("elements", [])[:max_elements]

    parts = []
    for elem in elements:
        parts.append(f"{elem.get('role', '')}|{elem.get('name', '')}|{elem['x']}|{elem['y']}")
    fingerprint_str = "\n".join(sorted(parts))

    hash_hex = hashlib.sha256(fingerprint_str.encode()).hexdigest()[:16]
    return {
        "hash": hash_hex,
        "element_count": len(elements),
    }


# ------------------------------------------------------------------
# MCP tool registration
# ------------------------------------------------------------------

def register(server) -> int:
    """Register find_element, click_element, list_elements, get_focused_element, smart_find, ui_fingerprint tools."""
    import base64 as _b64
    from mcp.server.fastmcp import Image as McpImage
    from openowl.tools.safety import with_timeout, ActionTimeoutError

    @server.tool()
    def find_element(
        name: str = "",
        role: str = "",
        window_title: str = "",
        index: int = 0,
    ) -> str:
        """Find a UI element by name and/or role using the accessibility tree.

        More reliable than coordinate guessing -- returns exact position and size.

        Parameters:
            name: Text label to search for (partial, case-insensitive match).
            role: Control type -- Button, Edit, MenuItem, TabItem, etc.
            window_title: Partial title of the target window (default: foreground).
            index: Which match to return if multiple (0 = first, default).
        """
        try:
            result = with_timeout(
                lambda: do_find_element(
                    name=name or None,
                    role=role or None,
                    window_title=window_title or None,
                    index=index,
                ),
                timeout=10.0,
            )
        except ActionTimeoutError:
            return f"Timed out after 10s searching for element name='{name}', role='{role}'. The accessibility tree may be unresponsive."
        if not result["found"]:
            return (
                f"No element found matching name='{name}', role='{role}'. Error: {result.get('error', 'none')}\n"
                f"💡 Tip: For web apps (Gmail, Google Chat, etc.), the accessibility tree often can't see inside browser content. "
                f"Try click_text(query=\"{name}\") instead — it uses OCR to find text on screen. "
                f"Or use keyboard shortcuts (Tab to navigate fields, Enter to submit)."
            )

        lines = [f"Found {len(result['elements'])} matching element(s):"]
        for i, elem in enumerate(result["elements"]):
            lines.append(
                f"[{i}] {elem['role']} \"{elem['name']}\" "
                f"({elem['x']},{elem['y']}) {elem['width']}x{elem['height']}"
            )
        return "\n".join(lines)

    @server.tool()
    def click_element(
        name: str = "",
        role: str = "",
        window_title: str = "",
        index: int = 0,
    ) -> list:
        """PRIMARY click tool. Click a UI element by name — works on native apps AND web apps.

        Uses a 3-tier cascade for maximum reliability:
          1. Accessibility tree (instant, pixel-perfect) — works for native apps
          2. Fuzzy accessibility match — handles partial/dynamic labels
          3. OCR text detection — finds visible text in web apps (Gmail, Slack, etc.)

        ALWAYS prefer this over click(x,y) for buttons, fields, links, menu items.
        Reports which element received focus after clicking — verify before typing.

        Parameters:
            name: Text label to search for (partial, case-insensitive). E.g. "Subject", "Compose", "Send".
            role: Optional control type hint — Button, Edit, MenuItem, TabItem, etc.
            window_title: Partial title of the target window (default: foreground).
            index: Which match to click if multiple (0 = first, default).
        """
        try:
            result = with_timeout(
                lambda: do_click_element(
                    name=name or None,
                    role=role or None,
                    window_title=window_title or None,
                    index=index,
                ),
                timeout=15.0,
            )
        except ActionTimeoutError:
            return f"Timed out after 15s trying to click '{name}'. Both accessibility and OCR searches exceeded time limit."
        if not result["success"]:
            return (
                f"Failed: {result['error']}\n"
                f"💡 Try: keyboard navigation (Tab to move between fields, Enter to submit), "
                f"or screenshot + click(x,y) as last resort."
            )

        elem = result["element"]
        method = result.get("method", "unknown")
        msg = (
            f"Clicked {elem['role']} \"{elem['name']}\" at "
            f"({result['clicked_at']['x']}, {result['clicked_at']['y']}) "
            f"via {method}."
        )
        if result.get("fallback"):
            msg += f"\n{result['note']}"
        if result.get("navigation_warning"):
            msg += f"\n{result['navigation_warning']}"
        # Surface focused element — critical for verifying correct field before typing
        fe = result.get("focused_element")
        if fe and (fe.get("role") or fe.get("name")):
            fname = fe.get("name", "")
            frole = fe.get("role", "")
            fval = fe.get("value", "")
            label = f'{frole} "{fname}"' if fname else frole
            if fval:
                label += f' (contains: "{fval[:80]}")'
            msg += f"\nNow focused on {label}."

        # Only include screenshot on failure/navigation (saves ~$0.02/call in vision tokens)
        needs_screenshot = result.get("navigation_warning") or result.get("fallback")
        if needs_screenshot:
            from openowl.tools.screenshot import capture_screenshot
            shot = capture_screenshot()
            return [
                McpImage(data=_b64.b64decode(shot["image"]), format="png"),
                msg,
            ]

        return msg

    @server.tool()
    def list_elements(
        window_title: str = "",
        max_depth: int = 5,
        role: str = "",
    ) -> str:
        """List accessible UI elements in a window. Use to discover what's clickable.

        Parameters:
            window_title: Partial title of the target window (default: foreground).
            max_depth: How deep to traverse the element tree (default 5). Ignored when role is set.
            role: Filter to this control type (e.g., "Spinner", "Button", "ComboBox"). When set, walks full tree.
        """
        try:
            result = with_timeout(
                lambda: do_list_elements(
                    window_title=window_title or None,
                    max_depth=max_depth,
                    role=role or None,
                ),
                timeout=10.0,
            )
        except ActionTimeoutError:
            return "Timed out after 10s listing UI elements. The accessibility tree may be unresponsive."
        if not result["elements"]:
            return f"No elements found. {result.get('error', '')}"

        header = f"Found {result['count']} elements"
        if role:
            header += f" with role '{role}'"
        header += ":"
        lines = [header]
        for i, elem in enumerate(result["elements"]):
            if not elem["name"] and elem["role"] in ("Pane", "Group", "Custom"):
                continue
            name_str = f"\"{elem['name']}\"" if elem["name"] else "(unnamed)"
            lines.append(
                f"[{i}] {elem['role']} {name_str} "
                f"({elem['x']},{elem['y']}) {elem['width']}x{elem['height']}"
            )
            if i >= 100:
                lines.append(f"... and {result['count'] - 100} more (use role filter to narrow)")
                break
        return "\n".join(lines)

    @server.tool()
    def get_focused_element() -> str:
        """Get the currently focused UI element's name, role, value, and position.

        Returns information about whatever widget currently has keyboard focus.
        Useful for confirming which field will receive typed input.
        """
        try:
            result = with_timeout(do_get_focused_element, timeout=5.0)
        except ActionTimeoutError:
            return "Timed out after 5s getting focused element."
        if not result["found"]:
            return f"Focus info unavailable. {result.get('error', 'UIA cannot see the focused widget in this application.')}"
        e = result["element"]
        value_str = f" value=\"{e['value']}\"" if e.get("value") else ""
        return (
            f"Focused: {e['role']} \"{e['name']}\"{value_str} "
            f"at ({e['x']},{e['y']}) {e['width']}x{e['height']}"
        )

    @server.tool()
    def smart_find(
        name: str,
        role: str = "",
        window_title: str = "",
        index: int = 0,
    ) -> str:
        """Find a UI element using accessibility tree first, then OCR fallback.

        More reliable than find_element alone — automatically falls back to OCR
        when the accessibility tree can't see the element (common with Qt, Electron,
        Java apps).

        Parameters:
            name: Text label to search for (partial, case-insensitive match).
            role: Control type hint (only used for accessibility search, ignored by OCR).
            window_title: Partial title of the target window (default: foreground).
            index: Which match to return if multiple (0 = first, default).
        """
        try:
            result = with_timeout(
                lambda: do_smart_find(
                    name=name,
                    role=role or None,
                    window_title=window_title or None,
                    index=index,
                ),
                timeout=15.0,
            )
        except ActionTimeoutError:
            return f"Timed out after 15s searching for '{name}'. Both accessibility and OCR failed to respond."
        if not result["found"]:
            return f"Not found: {result.get('error', 'unknown')}"

        method = result["method"]
        lines = [f"Found {len(result['elements'])} match(es) via {method}:"]
        for i, elem in enumerate(result["elements"]):
            lines.append(
                f"[{i}] {elem['role']} \"{elem['name']}\" "
                f"({elem['x']},{elem['y']}) {elem['width']}x{elem['height']}"
            )
        return "\n".join(lines)

    @server.tool()
    def ui_fingerprint(window_title: str = "") -> str:
        """Quick hash of the current UI layout for change detection.

        Returns a short hash and element count. Compare hashes between
        actions to detect if the screen layout changed (modal appeared,
        tab switched, etc.) without re-querying the full accessibility tree.
        """
        try:
            result = with_timeout(
                lambda: do_ui_fingerprint(window_title=window_title or None),
                timeout=5.0,
            )
        except ActionTimeoutError:
            return "Timed out computing UI fingerprint."
        return f"UI fingerprint: {result['hash']} ({result['element_count']} elements)"

    return 6

