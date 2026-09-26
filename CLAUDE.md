# CLAUDE.md

Guidance for Claude Code when working in this repository. User-facing documentation lives in
`README.md` (Chinese); keep it in sync -- `tests/test_docs.py` enforces the tool list, the
environment variables and the internal links.

## Project

Windows-MCP is a Python MCP server (FastMCP) that operates the real Windows desktop: genuine
mouse/keyboard input through `SendInput` plus UI Automation (UIA) actions, verified after every
step. 22 tools, registered in `src/windows_mcp/tools/__init__.py`.

## Commands

```powershell
uv sync --extra dev                  # install (uv, not pip); build backend: setuptools
uv run pytest                        # unit tests -- never send real input
uv run ruff check src tests          # lint (line length 100)
uv run windows-mcp serve             # run the server (stdio)
$env:WINDOWS_MCP_DESKTOP_TESTS="1"; uv run pytest tests/desktop   # REAL input into a fixture app
```

Python 3.12+. Tests must pass on both dependency generations: the lock file (fastmcp 3 / mcp 1)
and current releases (fastmcp 4 / mcp 2).

## Architecture

- `__main__.py` -- click CLI (`serve`, `install`, `uninstall`, `auth`), FastMCP assembly, the
  `instructions` sent to the agent (OBSERVE -> ACT -> VERIFY), lifespan (builds `Desktop` and
  the WatchDog on the desktop thread).
- `runtime.py` -- the single desktop thread (COM initialised once, MTA). `with_analytics`
  runs every sync tool there by default; tools that never touch the UI pass
  `offload="background"`. Never call UIA or send input from another thread.
- `desktop/native_input.py` -- `SendInput` mouse/keyboard: virtual-desktop absolute coordinates,
  atomic chords, correct extended-key flags, UTF-16 Unicode text, `InputBlockedError`.
- `desktop/elements.py` -- live element search (`FindAllBuildCache`), `ElementRegistry` (`e#`
  references keyed by runtime id), Snapshot-label relocation, hit-testing/occlusion handling
  (`prepare_for_pointer`), semantic actions (`act`, via auto/input/uia), side-effect
  observation (`observe`/`describe_changes`), keyboard interlock (`ensure_foreground`).
- `desktop/service.py` -- `Desktop`: state capture, screenshots, click/type/scroll/move/shortcut,
  app launch (ShellExecuteEx on an STA helper thread, waits for the window), window commands.
- `tree/` -- accessibility-tree traversal for Snapshot (element budget, word nodes opt-in).
- `uia/` -- comtypes wrapper around UIAutomationCore (vendored from yinkaisheng/uiautomation).
- `infrastructure/` -- auth, OAuth, SSRF protection, config.toml, opt-in PostHog telemetry.

## Rules of thumb

- Prefer real input first; UIA patterns are the fallback (`via="auto"`). Win32 list/combo/
  trackbar pattern changes must be followed by the notification a user action would produce.
- Never type without `ensure_foreground`; never click an element without `prepare_for_pointer`.
- Report honestly: `Verified` / `NOT verified` / not verifiable, plus `Effects`.
- Tools raise on failure (FastMCP sets `isError`), never return error strings as success.
- Never `print()` to stdout (it is the stdio protocol channel); use `logging`.
- Ruff: line length 100, double quotes; type hints on signatures.
