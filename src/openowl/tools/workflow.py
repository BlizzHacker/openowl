"""Workflow recording and replay tools.

Provides MCP tools to:
  - record_workflow: Start/stop recording actions
  - replay_workflow: Replay a saved workflow
  - list_workflows: List saved workflows
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Optional


_OPENOWL_DATA = os.path.join(os.path.expanduser("~"), ".openowl")
_WORKFLOWS_DIR = os.path.join(_OPENOWL_DATA, "workflows")

# In-memory recording state
_recording = False
_current_steps: list[dict] = []
_current_name: str = ""


def is_recording() -> bool:
    """Check if a workflow is currently being recorded."""
    return _recording


def record_step(step: dict) -> None:
    """Add a step to the current recording (if active)."""
    if _recording:
        step["_timestamp"] = time.time()
        _current_steps.append(step)


def start_recording(name: str) -> dict:
    """Start recording a new workflow."""
    global _recording, _current_steps, _current_name
    if _recording:
        return {"success": False, "error": f"Already recording '{_current_name}'. Stop it first."}
    _recording = True
    _current_steps = []
    _current_name = name
    return {"success": True, "name": name, "message": f"Recording started: '{name}'. All actions will be captured."}


def stop_recording() -> dict:
    """Stop recording and save the workflow."""
    global _recording, _current_steps, _current_name
    if not _recording:
        return {"success": False, "error": "No recording in progress."}

    _recording = False

    if not _current_steps:
        return {"success": False, "error": "No actions recorded. Nothing saved."}

    # Calculate delays between steps
    for i in range(1, len(_current_steps)):
        _current_steps[i]["_delay"] = round(
            _current_steps[i]["_timestamp"] - _current_steps[i-1]["_timestamp"], 2
        )
    if _current_steps:
        _current_steps[0]["_delay"] = 0

    # Save to disk
    os.makedirs(_WORKFLOWS_DIR, exist_ok=True)
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in _current_name)
    path = os.path.join(_WORKFLOWS_DIR, f"{safe_name}.json")

    workflow = {
        "name": _current_name,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "step_count": len(_current_steps),
        "steps": _current_steps,
    }

    with open(path, "w") as f:
        json.dump(workflow, f, indent=2)

    count = len(_current_steps)
    _current_steps = []
    _current_name = ""

    return {"success": True, "name": workflow["name"], "steps": count, "path": path}


def list_workflows() -> list[dict]:
    """List all saved workflows."""
    if not os.path.exists(_WORKFLOWS_DIR):
        return []

    workflows = []
    for fname in sorted(os.listdir(_WORKFLOWS_DIR)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(_WORKFLOWS_DIR, fname)
        try:
            with open(path) as f:
                data = json.load(f)
            workflows.append({
                "name": data.get("name", fname),
                "steps": data.get("step_count", 0),
                "created": data.get("created", "unknown"),
                "file": fname,
            })
        except Exception:
            continue
    return workflows


def load_workflow(name: str) -> Optional[dict]:
    """Load a workflow by name."""
    if not os.path.exists(_WORKFLOWS_DIR):
        return None

    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    path = os.path.join(_WORKFLOWS_DIR, f"{safe_name}.json")

    if not os.path.exists(path):
        # Try fuzzy match
        for fname in os.listdir(_WORKFLOWS_DIR):
            if name.lower() in fname.lower():
                path = os.path.join(_WORKFLOWS_DIR, fname)
                break
        else:
            return None

    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def replay_workflow(name: str, speed: float = 1.0, replacements: Optional[dict] = None) -> dict:
    """Replay a saved workflow.

    Args:
        name: Workflow name to replay.
        speed: Speed multiplier (1.0 = original timing, 2.0 = 2x faster, 0.5 = half speed).
        replacements: Dict of text replacements for type_text steps.
                      e.g. {"old_email@test.com": "new_email@test.com"}
    """
    from openowl.tools.input_tools import do_click, do_type_text, do_send_keys, do_scroll
    from openowl.tools.screenshot import capture_screenshot

    workflow = load_workflow(name)
    if not workflow:
        return {"success": False, "error": f"Workflow '{name}' not found."}

    steps = workflow.get("steps", [])
    if not steps:
        return {"success": False, "error": "Workflow has no steps."}

    results = []
    for i, step in enumerate(steps):
        # Apply delay (adjusted by speed)
        delay = step.get("_delay", 0.5) / max(0.1, speed)
        if delay > 0 and i > 0:
            time.sleep(delay)

        tool = step.get("tool", "")
        args = step.get("args", {})

        try:
            if tool == "click":
                result = do_click(args["x"], args["y"],
                                  button=args.get("button", "left"),
                                  clicks=args.get("clicks", 1))
                results.append({"step": i+1, "tool": tool, "success": True})

            elif tool == "type_text":
                text = args.get("text", "")
                # Apply replacements
                if replacements:
                    for old, new in replacements.items():
                        text = text.replace(old, new)
                result = do_type_text(text, interval=args.get("interval", 0.02))
                results.append({"step": i+1, "tool": tool, "success": True, "text": text[:50]})

            elif tool == "send_keys":
                result = do_send_keys(args.get("keys", ""))
                results.append({"step": i+1, "tool": tool, "success": True})

            elif tool == "scroll":
                result = do_scroll(args["x"], args["y"], args["direction"],
                                   amount=args.get("amount"),
                                   pages=args.get("pages"))
                results.append({"step": i+1, "tool": tool, "success": True})

            elif tool == "click_text":
                from openowl.tools.ocr import do_click_text
                result = do_click_text(args.get("query", ""),
                                       occurrence=args.get("occurrence", 1))
                results.append({"step": i+1, "tool": tool, "success": result.get("success", False)})

            else:
                results.append({"step": i+1, "tool": tool, "success": False, "error": f"Unknown tool: {tool}"})

        except Exception as e:
            results.append({"step": i+1, "tool": tool, "success": False, "error": str(e)})

    succeeded = sum(1 for r in results if r.get("success"))
    return {
        "success": True,
        "name": workflow["name"],
        "total_steps": len(steps),
        "succeeded": succeeded,
        "failed": len(steps) - succeeded,
        "results": results,
    }


def delete_workflow(name: str) -> dict:
    """Delete a saved workflow."""
    if not os.path.exists(_WORKFLOWS_DIR):
        return {"success": False, "error": "No workflows directory."}

    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    path = os.path.join(_WORKFLOWS_DIR, f"{safe_name}.json")

    if not os.path.exists(path):
        return {"success": False, "error": f"Workflow '{name}' not found."}

    os.remove(path)
    return {"success": True, "name": name}


# ------------------------------------------------------------------
# MCP tool registration
# ------------------------------------------------------------------

def register(server) -> int:
    """Register workflow tools."""
    import base64 as _b64
    from mcp.server.fastmcp import Image as McpImage
    from openowl.tools.safety import with_timeout, ActionTimeoutError

    @server.tool()
    def record_workflow(action: str, name: str = "") -> str:
        """Start or stop recording a workflow. All actions between start/stop are saved.

        Parameters:
            action: "start" to begin recording, "stop" to save and finish.
            name: Name for the workflow (required for "start").
        """
        if action.lower() == "start":
            if not name:
                return "Error: provide a name for the workflow."
            result = start_recording(name)
        elif action.lower() == "stop":
            result = stop_recording()
        else:
            return f"Unknown action '{action}'. Use 'start' or 'stop'."

        if not result["success"]:
            return f"Error: {result['error']}"
        return result.get("message", f"Workflow '{result['name']}': {result.get('steps', 0)} steps saved.")

    @server.tool()
    def replay_workflow_tool(
        name: str,
        speed: float = 1.0,
        replacements: str = "",
    ) -> list:
        """Replay a previously recorded workflow.

        Parameters:
            name: Name of the workflow to replay.
            speed: Speed multiplier (1.0 = original timing, 2.0 = 2x faster).
            replacements: JSON string of text replacements, e.g. '{"old@email.com": "new@email.com"}'.
        """
        repl = None
        if replacements:
            try:
                repl = json.loads(replacements)
            except json.JSONDecodeError:
                return f"Invalid replacements JSON: {replacements}"

        try:
            result = with_timeout(
                lambda: replay_workflow(name, speed=speed, replacements=repl),
                timeout=120.0,
            )
        except ActionTimeoutError:
            return f"Workflow replay timed out after 120s."

        if not result["success"]:
            return f"Error: {result['error']}"

        from openowl.tools.screenshot import capture_screenshot
        shot = capture_screenshot()

        msg = (
            f"Replayed '{result['name']}': {result['succeeded']}/{result['total_steps']} steps succeeded"
            + (f", {result['failed']} failed" if result['failed'] else "")
        )
        return [
            McpImage(data=_b64.b64decode(shot["image"]), format="png"),
            msg,
        ]

    @server.tool()
    def list_workflows_tool() -> str:
        """List all saved workflows available for replay."""
        workflows = list_workflows()
        if not workflows:
            return "No workflows saved yet. Use record_workflow(action='start', name='...') to record one."

        lines = [f"Saved workflows ({len(workflows)}):"]
        for w in workflows:
            lines.append(f"  - {w['name']} ({w['steps']} steps, created {w['created']})")
        return "\n".join(lines)

    @server.tool()
    def delete_workflow_tool(name: str) -> str:
        """Delete a saved workflow.

        Parameters:
            name: Name of the workflow to delete.
        """
        result = delete_workflow(name)
        if not result["success"]:
            return f"Error: {result['error']}"
        return f"Deleted workflow '{result['name']}'."

    return 4
