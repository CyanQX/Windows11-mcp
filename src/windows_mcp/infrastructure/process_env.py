"""Independent frozen launches must not inherit another process's unpack lifetime."""

from __future__ import annotations

import os


def independent_environment(overrides: dict | None = None) -> dict[str, str]:
    env = dict(os.environ)
    if overrides:
        env.update(overrides)
    for name in list(env):
        if name.startswith("_PYI_") or name == "_MEIPASS2":
            env.pop(name)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env
