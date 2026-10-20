"""Environment for programs we launch (kicad-cli, the agent CLIs, terminals).

The packaged sidecar is a PyInstaller build. On Linux its bootloader points
LD_LIBRARY_PATH at the bundled libraries, and child processes inherit that, so a
system program like kicad-cli loads our copies instead of its own and fails to
start. PyInstaller keeps the user's original value in LD_LIBRARY_PATH_ORIG
(unset if there was none); hand children that instead.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping


def external_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    if getattr(sys, "frozen", False):
        orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if orig is None:
            env.pop("LD_LIBRARY_PATH", None)
        else:
            env["LD_LIBRARY_PATH"] = orig
    return env
