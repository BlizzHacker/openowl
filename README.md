# openowl

A project of the [Move Weight Foundation](https://foundation.moveweight.com), a
501(c)(3).

An MCP server that gives any AI assistant eyes and hands on your desktop — screenshots, clicking, typing, OCR, window management, accessibility-tree queries, workflow recording.

Apache-2.0 licensed. No account, no API key, no usage limits, no telemetry. 40+ tools.

> **Prefer a hosted version?** A managed cloud build with a signed binary, one-click setup, and email support is available at [openowl.dev](https://openowl.dev/?utm_source=github&utm_medium=readme&utm_campaign=oss-repo). Same code, no setup.

## Install

### From source (recommended)

```bash
git clone https://github.com/mihir-kanzariya/openowl
cd openowl
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[macos]"      # or [windows]
openowl --version
```

`pip install -e .` adds the `openowl` console script to the venv. Anywhere that venv is on `PATH`, the command is callable.

### From PyPI _(coming soon)_

```bash
pip install "openowl[macos]"   # or [windows]
# or zero-install via uvx:
uvx openowl
```

PyPI publish is queued for the first tagged release. Until then, install from source above.

## Configure your MCP client

The server speaks stdio. Point any MCP client at the `openowl` command — the path it lives at depends on your install method.

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "owl": {
      "command": "/absolute/path/to/openowl/.venv/bin/openowl"
    }
  }
}
```

Replace the path with the output of `which openowl` after activating your venv. (Once PyPI publish lands you'll be able to use `"command": "uvx", "args": ["openowl"]` instead.)

### Claude Code

```bash
claude mcp add owl --transport stdio -s user -- /absolute/path/to/openowl/.venv/bin/openowl
```

### Codex / Cline / any MCP client

Run the `openowl` binary over stdio. Same configuration shape — point your client at the absolute path.

## Permissions (macOS)

On first run, macOS will prompt to grant **Accessibility** and **Screen Recording** permissions. The server checks both at startup and prints which are missing. Open System Settings → Privacy & Security → grant access → restart your MCP client.

Permissions are remembered per-binary path. Reinstalling into the same venv keeps them; switching to `uvx` (which uses an ephemeral path) re-prompts every run, so for daily use prefer the venv install above.

## Tools (40)

| Category | Tools |
|---|---|
| **Screen capture** | `screenshot`, `screenshot_baseline`, `screenshot_diff`, `manage_screenshots`, `get_screen_size` |
| **Input** | `click`, `type_text`, `paste_text`, `send_keys`, `scroll`, `drag`, `hover`, `get_mouse_position` |
| **Click via search** | `click_text`, `click_in_region`, `click_element`, `find_text`, `find_element`, `smart_find` |
| **Windows** | `list_windows`, `focus_window`, `launch_app`, `set_target_window`, `get_target_window`, `virtual_desktop` |
| **UI Automation** | `list_elements`, `get_focused_element`, `ui_fingerprint`, `detect_framework` |
| **Watching** | `wait_for_change`, `start_watcher`, `stop_watcher`, `get_notifications` |
| **Workflow recording** | `record_workflow`, `replay_workflow_tool`, `list_workflows_tool`, `delete_workflow_tool` |
| **System** | `clipboard`, `batch_actions`, `configure_uac` |

Run `openowl --version` to print the version.

## Pre-built workflows

Looking for ready-made automation recipes? See **[mihir-kanzariya/openowl-templates](https://github.com/mihir-kanzariya/openowl-templates)** — a separate repo of pre-built workflows for openowl + Claude Code. Clone, configure, run.

## Platforms

- **macOS** — full support (Accessibility + Screen Recording + Vision OCR APIs via PyObjC).
- **Windows** — full support via pywinauto + Win32 + RapidOCR.
- **Linux** — not supported.

## Development

```bash
git clone https://github.com/mihir-kanzariya/openowl
cd openowl
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,macos]"   # or [dev,windows]
pytest                          # 20 tests
ruff check .
```

See `CONTRIBUTING.md` for the full workflow.

## License

Apache License 2.0. See `LICENSE` and `NOTICE` for details, including attribution for inherited code.

---

Maintained by [Mihir Kanzariya](https://github.com/mihir-kanzariya). For a hosted, managed version with a signed binary and email support, see [openowl.dev](https://openowl.dev/?utm_source=github&utm_medium=readme-footer&utm_campaign=oss-repo).
