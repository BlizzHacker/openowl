# Contributing

Thanks for your interest in contributing to openowl.

## Development setup

```bash
git clone https://github.com/mihir-kanzariya/openowl
cd openowl
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,macos]"   # or [dev,windows] on Windows
```

## Running tests

```bash
pytest
```

Tests are platform-gated; macOS-only tests skip automatically on other platforms.

## Linting

```bash
ruff check .
ruff format .
```

## Pull requests

- One topic per PR. Keep diffs small and focused.
- Add or update tests when changing behavior.
- Run `pytest` and `ruff check` locally before pushing.
- Follow the existing code style — no surprise refactors mixed into feature PRs.

## Filing issues

When reporting a bug, please include:

- OS + version (macOS 14.5 / Windows 11 / etc.)
- Python version (`python3 --version`)
- openowl version (`openowl --version`)
- The MCP client you're using (Claude Desktop, Claude Code, Codex, Cline, ...)
- Minimal reproduction steps
- The full traceback from stderr

## License

By contributing, you agree your contributions will be licensed under the Apache License, Version 2.0, the same license as the project. Preserving the `NOTICE` file (including the embedded MIT attribution for inherited code) is required for any redistribution.
