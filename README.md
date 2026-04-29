# openowl

An MCP server that gives any AI assistant eyes and hands on your desktop — screenshots, clicking, typing, OCR, window management, accessibility-tree queries.

Apache-2.0 licensed. No account, no API key, no usage limits, no telemetry.

## Install

### uvx (zero install)

```bash
uvx openowl
```

### pip

```bash
pip install openowl
# macOS:
pip install "openowl[macos]"
# Windows:
pip install "openowl[windows]"
```

## Configure your MCP client

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "owl": {
      "command": "uvx",
      "args": ["openowl"]
    }
  }
}
```

### Claude Code

```bash
claude mcp add owl --transport stdio -s user -- uvx openowl
```

### Codex / Cline / any MCP client

Run `openowl` over stdio. Same configuration shape — point your client at the `openowl` command.

## Permissions (macOS)

On first run, macOS will prompt to grant **Accessibility** and **Screen Recording** permissions. The server checks for both and prints which are missing.

If you install via `pip`, the binary path is stable and macOS remembers granted permissions across runs. If you use `uvx`, the path may change between invocations and macOS will re-prompt — for daily use, prefer `pip install openowl[macos]`.

## Tools

| Category | Tools |
|---|---|
| Screen | `screenshot`, `screenshot_diff` |
| Input | `click`, `click_text`, `click_in_region`, `type_text`, `paste_text`, `send_keys`, `scroll`, `drag`, `hover`, `clipboard` |
| Vision | `find_text`, `find_element`, `smart_find` |
| Windows | `list_windows`, `focus_window`, `get_target_window`, `set_target_window` |
| UI Automation | `list_elements`, `get_focused_element`, `click_element` |
| Workflow | `record_workflow`, `replay_workflow_tool`, `list_workflows_tool`, `delete_workflow_tool` |
| System | `launch_app`, `get_screen_size`, `get_mouse_position`, `detect_framework`, `configure_uac` |

Run `openowl --version` to print the version.

## Platforms

- **macOS** — full support (Accessibility + Screen Recording APIs).
- **Windows** — full support via pywinauto + Win32.
- **Linux** — not supported.

## Development

```bash
git clone https://github.com/mihir-kanzariya/openowl
cd openowl
pip install -e ".[dev,macos]"   # or [dev,windows]
pytest
```

See `CONTRIBUTING.md` for the full workflow.

## License

Apache License 2.0. See `LICENSE` and `NOTICE` for details, including attribution for inherited code.
