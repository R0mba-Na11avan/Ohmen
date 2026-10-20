"""Sidecar entry point the desktop app launches: `python -m pcb_assistant.server --port N`.

It is also the script the packaged (PyInstaller) sidecar is frozen from, so it
hands uvicorn the app object rather than an import string a freezer can't follow.
Loopback only: the sidecar holds the user's API keys, so it also refuses to run
without the per-launch PCB_SIDECAR_TOKEN the app passes (see server/auth.py),
unless told --no-auth for local testing.
"""
from __future__ import annotations

import argparse
import os
import sys

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="ohmen-sidecar")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--log-level", default="warning")
    parser.add_argument("--no-auth", action="store_true", help="Run without the access token (local testing only).")
    args = parser.parse_args()
    if not args.no_auth and not os.environ.get("PCB_SIDECAR_TOKEN"):
        sys.exit("ohmen-sidecar: PCB_SIDECAR_TOKEN is not set. The desktop app sets it; "
                 "for local testing use `pcb serve` (prints a token) or pass --no-auth.")

    from pcb_assistant.server.app import app  # builds the token guard from the environment

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
