"""Session context — tracks recent actions for continuity.

Maintains a rolling history of the last N actions so tool responses
can include what happened before. This helps the AI understand
context without re-querying the screen.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class ActionRecord:
    """A single recorded action."""
    tool: str
    summary: str
    timestamp: float = field(default_factory=time.time)
    coordinates: Optional[dict] = None  # {"x": int, "y": int}
    visual_change: Optional[bool] = None
    target_text: Optional[str] = None
    window_title: Optional[str] = None


class SessionContext:
    """Rolling action history for a single MCP session."""

    def __init__(self, max_history: int = 10):
        self._history: deque[ActionRecord] = deque(maxlen=max_history)
        self._target_window: Optional[str] = None
        self._session_start = time.time()

    def record(self, action: ActionRecord) -> None:
        """Record an action."""
        self._history.append(action)

    def record_click(self, x: int, y: int, visual_change: bool,
                     target_text: str = "", window_title: str = "") -> None:
        """Convenience: record a click action."""
        self._history.append(ActionRecord(
            tool="click",
            summary=f"Clicked at ({x},{y})" + (f' on "{target_text}"' if target_text else ""),
            coordinates={"x": x, "y": y},
            visual_change=visual_change,
            target_text=target_text or None,
            window_title=window_title or None,
        ))

    def record_type(self, text: str) -> None:
        """Convenience: record a type action."""
        preview = text[:50] + "..." if len(text) > 50 else text
        self._history.append(ActionRecord(
            tool="type_text",
            summary=f'Typed "{preview}"',
            target_text=text,
        ))

    def record_screenshot(self, width: int, height: int) -> None:
        """Convenience: record a screenshot action."""
        self._history.append(ActionRecord(
            tool="screenshot",
            summary=f"Screenshot {width}x{height}",
        ))

    def record_keys(self, keys: str) -> None:
        """Convenience: record a send_keys action."""
        self._history.append(ActionRecord(
            tool="send_keys",
            summary=f"Sent keys: {keys}",
        ))

    def get_summary(self, last_n: int = 5) -> str:
        """Return a concise summary of recent actions.

        Returns empty string if no actions recorded.
        """
        if not self._history:
            return ""

        recent = list(self._history)[-last_n:]
        lines = ["\nRecent actions:"]
        for i, action in enumerate(recent):
            age = time.time() - action.timestamp
            if age < 60:
                age_str = f"{age:.0f}s ago"
            else:
                age_str = f"{age/60:.0f}m ago"

            change_str = ""
            if action.visual_change is not None:
                change_str = " ✓" if action.visual_change else " (no change)"

            lines.append(f"  {i+1}. [{age_str}] {action.summary}{change_str}")

        return "\n".join(lines)

    def get_last_action(self) -> Optional[ActionRecord]:
        """Return the most recent action, or None."""
        return self._history[-1] if self._history else None

    def get_change_summary(self) -> str:
        """Summarize what changed since last screenshot.

        Looks at actions after the last screenshot to describe
        what the AI has done since it last saw the screen.
        """
        if not self._history:
            return ""

        # Find last screenshot
        actions_since = []
        for action in reversed(self._history):
            if action.tool == "screenshot":
                break
            actions_since.insert(0, action)

        if not actions_since:
            return ""

        lines = ["Since last screenshot:"]
        for action in actions_since:
            lines.append(f"  - {action.summary}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialize for debugging."""
        return {
            "history": [asdict(a) for a in self._history],
            "session_age_seconds": time.time() - self._session_start,
        }


# Global session instance — one per MCP server process
_session = SessionContext()


def get_session() -> SessionContext:
    """Get the global session context."""
    return _session
