# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-19

First public release.

### Added

- Desktop app for Windows and Linux (installer, rpm, deb, AppImage).
- Schematic review: KiCad ERC/DRC, deterministic datasheet-cited patterns, and an
  AI reviewer team with an evidence gate.
- Copilot chat with tools, chat sessions per project, image attachments, an
  effort control (Low to Max) and a Stop button that cancels the reply in the
  engine too.
- Datasheet library with automatic fetch, search and page-level citations.
- BOM from the schematic, with optional DigiKey stock, pricing and lifecycle.
  Planned parts merge into their schematic rows once placed.
- Block diagram of power and signal flow.
- Providers: Anthropic, Google Gemini, OpenAI, local OpenAI-compatible servers;
  experimental Codex and Claude Code CLI routes.
- `pcb` command-line interface.

### Known limitations

- The Windows installer isn't code-signed yet, so SmartScreen warns on first run.
- No macOS build.
- Needs KiCad 9 or newer for ERC/DRC and netlist export.
- On Linux without a keyring (no GNOME Keyring or KWallet), saved API keys are
  only obfuscated, not encrypted; the provider settings say so when it happens.
- The effort control is off for models that don't reason by default (OpenAI API
  models, Claude Haiku, local models).
- Stopping a reply mid design-review stops the chat, but reviewers already
  running finish their current call.

[0.1.0]: https://github.com/R0mba-Na11avan/Ohmen/releases/tag/v0.1.0
