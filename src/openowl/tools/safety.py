"""Safety mechanisms replacing pyautogui.FAILSAFE.

Provides:
    with_timeout       - wrap any action in a timeout
    CircuitBreaker     - track consecutive failures, open circuit after threshold
    check_desktop_guard - warn if not on isolated desktop
    ActionTimeoutError - raised on timeout
    CircuitOpenError   - raised when circuit is open
"""

import sys
import threading
from typing import Any, Callable

_SENTINEL = object()


class ActionTimeoutError(Exception):
    """Raised when a tool action exceeds its timeout."""
    pass


class CircuitOpenError(Exception):
    """Raised when too many consecutive failures have occurred."""
    pass


def with_timeout(fn: Callable, timeout: float = 10.0, default: Any = _SENTINEL) -> Any:
    """Run *fn()* with a timeout.

    Parameters
    ----------
    fn : callable
        Zero-argument callable to execute.
    timeout : float
        Maximum seconds to wait.
    default : Any
        If provided, return this value on timeout instead of raising.

    Raises
    ------
    ActionTimeoutError
        If *fn* exceeds *timeout* and no *default* was given.
    """
    result = [None]
    error = [None]

    def wrapper():
        try:
            result[0] = fn()
        except Exception as e:
            error[0] = e

    t = threading.Thread(target=wrapper, daemon=True)
    t.start()
    t.join(timeout=timeout)

    if t.is_alive():
        print(f"[openowl] TIMEOUT: action exceeded {timeout}s", file=sys.stderr)
        if default is not _SENTINEL:
            return default
        raise ActionTimeoutError(f"Action timed out after {timeout}s")
    if error[0]:
        raise error[0]
    return result[0]


class CircuitBreaker:
    """Track consecutive failures. Opens circuit after max_failures."""

    def __init__(self, max_failures: int = 5):
        self.max_failures = max_failures
        self.consecutive_failures = 0

    @property
    def is_open(self) -> bool:
        return self.consecutive_failures >= self.max_failures

    def record_success(self):
        self.consecutive_failures = 0

    def record_failure(self):
        self.consecutive_failures += 1

    def check(self):
        """Raise CircuitOpenError if circuit is open."""
        if self.is_open:
            raise CircuitOpenError(
                f"{self.consecutive_failures} consecutive failures. "
                f"Take a fresh screenshot and reassess before retrying."
            )


# Reference to desktop state from desktop.py
# Imported lazily to avoid circular imports
_desktop_state = None


def check_desktop_guard() -> str | None:
    """Check if on an isolated desktop. Returns info string or None.

    Virtual desktop isolation is OPTIONAL -- v0.2's accessibility
    targeting and OCR make it safe to work on the user's desktop
    directly. This function is informational, not a warning.
    """
    global _desktop_state
    if _desktop_state is None:
        try:
            from openowl.tools.desktop import _desktop_state as ds
            _desktop_state = ds
        except ImportError:
            return None

    if _desktop_state.get("on_isolated", False):
        return "On isolated desktop."
    return None

