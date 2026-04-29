"""Manage tools -- clipboard access and screenshot management.

Provides two utility MCP tools:
    clipboard          - read from / write to the system clipboard
    manage_screenshots - list, cleanup, or set rolling cap on saved screenshots

Core functions:
    do_clipboard_read   - read clipboard contents via pyperclip
    do_clipboard_write  - write text to clipboard via pyperclip

MCP registration:
    register            - wire clipboard and manage_screenshots on a Server
"""

import pyperclip


# ------------------------------------------------------------------
# Helper / validation functions
# ------------------------------------------------------------------

def validate_screenshot_action(action: str) -> str:
    """Validate and normalise a screenshot management action.

    Raises :class:`ValueError` if *action* is not one of
    ``list``, ``cleanup``, ``set_limit``.
    """
    valid = ("list", "cleanup", "set_limit")
    if action.lower() not in valid:
        raise ValueError(
            f"Invalid action '{action}'. Must be one of: {valid}"
        )
    return action.lower()


def validate_clipboard_action(action: str) -> str:
    """Validate and normalise a clipboard action.

    Raises :class:`ValueError` if *action* is not one of
    ``read``, ``write``.
    """
    valid = ("read", "write")
    if action.lower() not in valid:
        raise ValueError(
            f"Invalid action '{action}'. Must be one of: {valid}"
        )
    return action.lower()


# ------------------------------------------------------------------
# Core functions
# ------------------------------------------------------------------

def do_clipboard_read() -> str:
    """Read and return the current clipboard contents."""
    return pyperclip.paste()


def do_clipboard_write(text: str):
    """Write *text* to the system clipboard."""
    pyperclip.copy(text)


# ------------------------------------------------------------------
# MCP tool registration
# ------------------------------------------------------------------

def register(server) -> int:
    """Register *clipboard* and *manage_screenshots* tools on *server*.

    Returns the number of tools registered (2).
    """

    from openowl.tools.safety import with_timeout, ActionTimeoutError

    @server.tool()
    def clipboard(action: str, text: str = "") -> str:
        """Read from or write to the system clipboard.

        Parameters:
            action: "read" to get clipboard contents, "write" to set them.
            text: The text to write (only used when action is "write").
        """
        action = validate_clipboard_action(action)
        if action == "read":
            try:
                return with_timeout(do_clipboard_read, timeout=3.0)
            except ActionTimeoutError:
                return "Timed out after 3s reading clipboard. Another app may be holding the clipboard lock."
        try:
            with_timeout(lambda: do_clipboard_write(text), timeout=3.0)
        except ActionTimeoutError:
            return "Timed out after 3s writing to clipboard. Another app may be holding the clipboard lock."
        return f"Copied {len(text)} characters to clipboard."

    @server.tool()
    def manage_screenshots(action: str, keep: int = 50) -> str:
        """Manage saved screenshots: list, cleanup, or change the rolling cap.

        Parameters:
            action: One of "list", "cleanup", "set_limit".
            keep: New rolling cap (only used when action is "set_limit", default 50).
        """
        from server import screenshot_mgr

        action = validate_screenshot_action(action)

        if action == "list":
            screenshots = screenshot_mgr.list_screenshots()
            count = len(screenshots)
            limit = screenshot_mgr._max_screenshots
            if count == 0:
                return f"No saved screenshots. Limit: {limit}."
            lines = [f"Saved screenshots: {count} (limit: {limit})"]
            for s in screenshots:
                lines.append(f"  {s['name']}  ({s['size']} bytes)")
            return "\n".join(lines)

        elif action == "cleanup":
            removed = screenshot_mgr.cleanup()
            return f"Removed {removed} screenshots."

        else:  # set_limit
            screenshot_mgr.set_limit(keep)
            remaining = len(screenshot_mgr.list_screenshots())
            return f"Screenshot limit set to {keep}. Current count: {remaining}."

    return 2

