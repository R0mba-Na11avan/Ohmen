"""Local sidecar HTTP API (Phase 2).

A thin FastAPI layer the Electron desktop app talks to over 127.0.0.1. Every
route is a wrapper over the already-tested reasoning core; no product logic lives
here. Bound to loopback only; API keys stay server-side and never reach the
renderer.
"""
