"""Batch action tool -- execute a sequence of input actions in one call.

Provides one MCP tool:
    batch_actions  - run a list of click/type/keys/wait/scroll actions, return one screenshot

Reduces round-trips for common data entry sequences like click-type-enter.
"""

import time
from typing import List

from openowl.tools.input_tools import do_click, do_type_text, do_send_keys, do_scroll


_ACTION_HANDLERS = {
    "click": lambda a: do_click(
        a["x"], a["y"],
        button=a.get("button", "left"),
        clicks=a.get("clicks", 1),
    ),
    "type": lambda a: do_type_text(
        a["text"],
        interval=a.get("interval", 0.02),
    ),
    "keys": lambda a: do_send_keys(a["keys"]),
    "scroll": lambda a: do_scroll(
        a["x"], a["y"], a["direction"],
        amount=a.get("amount"),
        pages=a.get("pages"),
    ),
    "wait": lambda a: time.sleep(a["ms"] / 1000.0),
}

# Required fields per action type
_REQUIRED_FIELDS = {
    "click": ["x", "y"],
    "type": ["text"],
    "keys": ["keys"],
    "scroll": ["x", "y", "direction"],
    "wait": ["ms"],
}


def do_batch_actions(actions: List[dict]) -> dict:
    """Execute a sequence of actions.

    Returns dict with 'success', 'executed' count, and 'summary'.
    """
    executed = 0
    summary_parts = []

    for i, action in enumerate(actions):
        action_type = action.get("action", "")
        if action_type not in _ACTION_HANDLERS:
            return {
                "success": False,
                "executed": executed,
                "error": f"Unknown action '{action_type}' at index {i}. Valid: {list(_ACTION_HANDLERS.keys())}",
            }

        # Validate required fields
        for field in _REQUIRED_FIELDS.get(action_type, []):
            if field not in action:
                return {
                    "success": False,
                    "executed": executed,
                    "error": f"Missing required field '{field}' for '{action_type}' at index {i}.",
                }

        try:
            _ACTION_HANDLERS[action_type](action)
            executed += 1
            summary_parts.append(action_type)
        except Exception as e:
            return {
                "success": False,
                "executed": executed,
                "error": f"Action '{action_type}' at index {i} failed: {e}",
            }

    return {
        "success": True,
        "executed": executed,
        "summary": ", ".join(summary_parts),
    }


# ------------------------------------------------------------------
# MCP tool registration
# ------------------------------------------------------------------

def register(server) -> int:
    """Register the *batch_actions* tool on *server*."""
    import base64 as _b64
    from mcp.server.fastmcp import Image as McpImage
    from openowl.tools.safety import with_timeout, ActionTimeoutError

    @server.tool()
    def batch_actions(actions: list[dict]) -> list:
        """Execute a sequence of input actions and return one screenshot.

        Reduces round-trips for common sequences like click-type-enter.
        Each action is a dict with an "action" key and action-specific parameters.

        Supported actions:
            {"action": "click", "x": int, "y": int, "button": "left", "clicks": 1}
            {"action": "type", "text": str, "interval": 0.02}
            {"action": "keys", "keys": str}  (e.g. "enter", "ctrl+s")
            {"action": "scroll", "x": int, "y": int, "direction": str, "amount": 50, "pages": 1}  (pages uses PageDown/PageUp keys)
            {"action": "wait", "ms": int}

        Parameters:
            actions: List of action dicts to execute in order.
        """
        if not actions:
            return "No actions to execute."

        # Calculate timeout: sum of per-action defaults
        timeout_map = {"click": 5, "type": 10, "keys": 5, "scroll": 3, "wait": 0}
        total_timeout = 5.0  # base grace
        for a in actions:
            atype = a.get("action", "")
            if atype == "wait":
                total_timeout += a.get("ms", 0) / 1000.0
            else:
                total_timeout += timeout_map.get(atype, 5)
        total_timeout = min(total_timeout, 120.0)  # cap at 2 minutes

        try:
            result = with_timeout(
                lambda: do_batch_actions(actions),
                timeout=total_timeout,
            )
        except ActionTimeoutError:
            return f"Timed out after {total_timeout:.0f}s executing batch actions."

        if not result["success"]:
            return f"Batch failed after {result['executed']} action(s): {result['error']}"

        from openowl.tools.screenshot import capture_screenshot
        shot = capture_screenshot()

        return [
            McpImage(data=_b64.b64decode(shot["image"]), format="png"),
            f"Executed {result['executed']} action(s): {result['summary']}. Screenshot: {shot['width']}x{shot['height']}",
        ]

    return 1

