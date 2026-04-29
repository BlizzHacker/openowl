"""UAC tools -- manage Windows UAC consent prompts.

Provides one MCP tool:
    configure_uac  - suppress, restore, or query UAC consent behavior

Approach:
    "suppress" sets BOTH ConsentPromptBehaviorAdmin=0 (auto-approve elevation)
    and PromptOnSecureDesktop=0 (UAC on normal desktop so openowl can interact).
    This requires ONE manual UAC approval the first time.

    "restore" puts both values back to their originals.

    "status" reports current values.

Windows-only.  On other platforms the tool returns an explanatory message.
"""

from __future__ import annotations

import platform
import subprocess
import time

_REG_PATH = r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"

# Saved originals for restore
_originals: dict[str, int] = {}


def _is_windows() -> bool:
    return platform.system().lower() == "windows"


def _read_reg_value(name: str) -> int | None:
    """Read a DWORD from the UAC registry path."""
    if not _is_windows():
        return None
    try:
        result = subprocess.run(
            ["reg", "query", _REG_PATH, "/v", name],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            line = line.strip()
            if name in line and "REG_DWORD" in line:
                parts = line.split()
                return int(parts[-1], 16)
        return None
    except Exception:
        return None


def _set_reg_elevated(name: str, value: int) -> dict:
    """Set a UAC registry DWORD via an elevated process."""
    reg_cmd = (
        f'reg add "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System" '
        f'/v {name} /t REG_DWORD /d {value} /f'
    )
    try:
        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                f'Start-Process cmd -Verb RunAs -Wait -WindowStyle Hidden '
                f'-ArgumentList \'/c {reg_cmd}\''
            ],
            capture_output=True, text=True, timeout=30
        )
        actual = _read_reg_value(name)
        if actual == value:
            return {"success": True}
        return {
            "success": False,
            "error": f"{name} is {actual}, expected {value}. UAC prompt may have been denied."
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Timed out waiting for elevated process"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _set_multiple_elevated(changes: dict[str, int]) -> dict:
    """Set multiple registry values in a single elevated cmd."""
    cmds = []
    for name, value in changes.items():
        cmds.append(
            f'reg add "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System" '
            f'/v {name} /t REG_DWORD /d {value} /f'
        )
    combined = " && ".join(cmds)
    try:
        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                f'Start-Process cmd -Verb RunAs -Wait -WindowStyle Hidden '
                f'-ArgumentList \'/c {combined}\''
            ],
            capture_output=True, text=True, timeout=30
        )
        # Verify all changes
        failures = []
        for name, value in changes.items():
            actual = _read_reg_value(name)
            if actual != value:
                failures.append(f"{name}: got {actual}, expected {value}")
        if not failures:
            return {"success": True}
        return {"success": False, "error": "; ".join(failures)}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Timed out waiting for elevated process"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _auto_approve_uac():
    """Send Left Arrow + Enter to approve a UAC dialog on the normal desktop."""
    from openowl.tools.safety import with_timeout

    def _do_approve():
        import pyautogui
        time.sleep(1.5)
        pyautogui.press("left")
        time.sleep(0.1)
        pyautogui.press("enter")
        time.sleep(1.0)

    try:
        with_timeout(_do_approve, timeout=5.0)
    except Exception:
        pass


# ------------------------------------------------------------------
# Core function
# ------------------------------------------------------------------

def do_configure_uac(action: str) -> str:
    global _originals

    if not _is_windows():
        return "UAC management is Windows-only. No action taken."

    action = action.lower().strip()
    valid = ("suppress", "restore", "status")
    if action not in valid:
        raise ValueError(f"Invalid action '{action}'. Must be one of: {valid}")

    if action == "status":
        consent = _read_reg_value("ConsentPromptBehaviorAdmin")
        secure = _read_reg_value("PromptOnSecureDesktop")
        if consent is None and secure is None:
            return "Could not read UAC settings."

        consent_desc = {
            0: "0 - Elevate without prompting (suppressed)",
            1: "1 - Prompt for credentials on secure desktop",
            2: "2 - Prompt for consent on secure desktop",
            3: "3 - Prompt for credentials",
            4: "4 - Prompt for consent",
            5: "5 - Prompt for consent for non-Windows binaries (default)",
        }
        secure_desc = {
            0: "0 - Normal desktop (openowl can auto-approve)",
            1: "1 - Secure desktop (manual approval required)",
        }

        lines = []
        if consent is not None:
            lines.append(f"ConsentPromptBehaviorAdmin = {consent_desc.get(consent, str(consent))}")
        if secure is not None:
            lines.append(f"PromptOnSecureDesktop = {secure_desc.get(secure, str(secure))}")
        if _originals:
            lines.append(f"Saved originals: {_originals}")
        return "\n".join(lines)

    elif action == "suppress":
        consent = _read_reg_value("ConsentPromptBehaviorAdmin")
        secure = _read_reg_value("PromptOnSecureDesktop")

        if consent is None or secure is None:
            return "Could not read current UAC settings. Cannot proceed."

        if consent == 0 and secure == 0:
            return "UAC is already fully suppressed."

        # Save originals (only if not already saved — don't overwrite mid-session)
        if not _originals:
            _originals = {
                "ConsentPromptBehaviorAdmin": consent,
                "PromptOnSecureDesktop": secure,
            }

        # Build changes needed
        changes = {}
        if consent != 0:
            changes["ConsentPromptBehaviorAdmin"] = 0
        if secure != 0:
            changes["PromptOnSecureDesktop"] = 0

        result = _set_multiple_elevated(changes)
        if result["success"]:
            changed = ", ".join(f"{k}: {_originals[k]} -> 0" for k in changes)
            return (
                f"UAC suppressed. {changed}\n"
                f"Original values saved for restore.\n"
                f"All elevation requests will now auto-approve silently."
            )
        else:
            _originals = {}
            return f"Failed to suppress UAC: {result['error']}"

    else:  # restore
        consent = _read_reg_value("ConsentPromptBehaviorAdmin")
        secure = _read_reg_value("PromptOnSecureDesktop")

        if _originals:
            restore_to = _originals
        else:
            # No saved originals — restore to Windows defaults
            restore_to = {
                "ConsentPromptBehaviorAdmin": 5,
                "PromptOnSecureDesktop": 1,
            }

        # Build changes needed
        changes = {}
        if consent != restore_to.get("ConsentPromptBehaviorAdmin"):
            changes["ConsentPromptBehaviorAdmin"] = restore_to["ConsentPromptBehaviorAdmin"]
        if secure != restore_to.get("PromptOnSecureDesktop"):
            changes["PromptOnSecureDesktop"] = restore_to["PromptOnSecureDesktop"]

        if not changes:
            _originals = {}
            return "UAC is already at target values. No changes needed."

        # If UAC is currently on normal desktop, auto-approve the restore elevation
        if secure == 0:
            import threading
            t = threading.Thread(target=_auto_approve_uac, daemon=True)
            t.start()

        result = _set_multiple_elevated(changes)
        if result["success"]:
            restored = ", ".join(f"{k}: {v}" for k, v in changes.items())
            _originals = {}
            return f"UAC restored. {restored}"
        else:
            return f"Failed to restore UAC: {result['error']}"


# ------------------------------------------------------------------
# MCP tool registration
# ------------------------------------------------------------------

def register(server) -> int:
    """Register the *configure_uac* tool on *server*."""

    @server.tool()
    def configure_uac(action: str) -> str:
        """Manage Windows UAC consent prompts. Requires user permission upfront.

        When suppressed, all elevation requests auto-approve without a UAC dialog.
        The first "suppress" call triggers ONE UAC prompt to gain admin rights.
        Always restore when done.

        Parameters:
            action: One of "suppress" (disable UAC prompts), "restore" (re-enable),
                    or "status" (check current setting).
        """
        return do_configure_uac(action)

    return 1

