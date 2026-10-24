# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Footprints generated from a datasheet's package drawing (SOIC/SOT/QFP, DFN/QFN,
  chip parts, pin headers, or an explicit land pattern), with IPC-7351B land
  math. The agent asks first, and nothing is written until you accept the card,
  which shows a preview and every dimension with its datasheet page. Accepted
  footprints go to `ohmen.pretty`, registered in the project's library table.
- DigiKey search and part lookup tools for the agent, and parts research that
  picks candidates from real DigiKey results instead of memory.
- The agent can read datasheets for parts that aren't on the board, and any PDF
  by https link. These stay in a hidden `datasheets/.research/` folder until the
  part goes on the BOM.
- Token usage per reply (fresh, cache write, cache read, output), also logged
  to `.pcb_assistant/usage.jsonl`.
- Long chats condense: past a size (Settings > General: Off, Normal ~40k,
  Aggressive ~20k, or Custom), or after an hour away, older turns become a
  summary written by a small model on the same route, and the latest turns stay
  word for word. The chat shows where it happened and what the summary says;
  each chat row has Condense now.
- Local models take a context window in Settings (8k was always assumed).
- Footprint cards show KiCad's drawing of the file and KiCad's own read-back
  of the pads, compared with the pad table; zoom in for pad numbers.
- Your chat messages render Markdown.

### Fixed

- Equations in chat replies render as math (KaTeX) instead of raw LaTeX.

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
