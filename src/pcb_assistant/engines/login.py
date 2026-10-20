"""Open the CLI's own login flow in a visible terminal window.

Login is interactive (browser OAuth, device codes), so it runs where the user
can see it. We only start it; the CLI stores its token itself.
"""
from __future__ import annotations

import shlex
import shutil
import subprocess
import sys

from pcb_assistant.procenv import external_env


def command(engine: str, path: str) -> list[str]:
    return [path, "auth", "login"] if engine == "claude" else [path, "login"]


def open_terminal(cmd: list[str]) -> None:
    if sys.platform == "win32":
        # `start` opens a new console; the empty "" is its window title.
        subprocess.Popen(["cmd", "/c", "start", "", "cmd", "/k", subprocess.list2cmdline(cmd)])
    elif sys.platform == "darwin":
        script = shlex.join(cmd).replace("\\", "\\\\").replace('"', '\\"')
        subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{script}"'])
    else:
        subprocess.Popen(_linux_terminal(cmd), env=external_env())


# How each common Linux terminal takes a command to run, in preference order.
# x-terminal-emulator is Debian/Ubuntu's alias for the desktop's default;
# Ptyxis is Fedora's default since 41.
_LINUX_TERMINALS = (
    ("x-terminal-emulator", ["-e"]),
    ("ptyxis", ["--new-window", "--"]),
    ("gnome-terminal", ["--"]),
    ("konsole", ["-e"]),
    ("xfce4-terminal", ["-x"]),
    ("xterm", ["-e"]),
)


def _linux_terminal(cmd: list[str]) -> list[str]:
    # Keep the window open after login so the user can read the CLI's result.
    inner = ["sh", "-c", shlex.join(cmd) + "; echo; printf 'Press Enter to close. '; read _"]
    for name, flags in _LINUX_TERMINALS:
        if exe := shutil.which(name):
            return [exe, *flags, *inner]
    raise OSError(f"no terminal emulator found; run `{shlex.join(cmd)}` in a terminal")
