# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.0] — Unreleased

Initial public release.

### Added
- FastMCP-based MCP server exposing 30+ tools for desktop automation:
  screenshots, clicking, typing, OCR, window management, accessibility-tree
  queries, workflow recording/replay, framework detection, screen diffing.
- macOS backend via PyObjC (Accessibility + Screen Recording APIs).
- Windows backend via pywinauto + Win32.
- `pip install openowl` and `uvx openowl` installation paths.
- Console entry point: `openowl` (stdio transport).
- macOS permissions preflight at startup with system-settings deep-link.
- Workflow data dir: `~/.openowl/workflows/`.
