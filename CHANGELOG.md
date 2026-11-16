# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Footprints for connectors and modules. A land pattern can now have rows of
  pads, slotted and non-plated holes, turned pads, pads without paste or mask,
  chamfered pads, and a body that is offset or not a rectangle.
- Footprints that follow a symbol. The copilot reads a symbol's pins from the
  schematic or the project's symbol libraries, numbers the pads after them, and
  the card shows each pad's pin name and whether every pin has a pad.
- Silkscreen you can ask for: corner marks or no outline, a dot, line or
  triangle at pin 1, a cathode bar or a +, labels beside pads, and the
  copilot's own lines, arcs, circles, polygons and text. Silkscreen that runs
  over a pad is trimmed off it and the card says so; text on a pad is refused.
- Board cutouts and keepout areas in a footprint, each with its source. The
  card warns in red when a footprint changes the board, and KiCad's read-back
  confirms the cutout and keepout survived.

### Changed

- Footprints always go through the footprint tool. Asking for one proposes it
  straight away (the approval card is still the gate), and a match in KiCad's
  library is named instead of generating a duplicate. Claude Code with all
  tools on can no longer write a `.kicad_mod` file by hand, even with a tool
  your own Claude Code settings always allow. If a footprint file changes
  during a reply in which the copilot ran a shell command, the chat names it
  and the copilot is told to rebuild it if it wrote it.
- The copilot can read back a footprint Ohmen made and propose a changed one.
- The chat folds the copilot's tool calls behind a "steps" line once its answer
  starts. Calls still show as they happen, and reviews, footprints, diagram
  and BOM changes keep a line of their own.

## [0.1.2] - 2026-11-11

### Added

- Update checks. Installed copies look for a newer GitHub release shortly after
  launch and every few hours. The Windows installer and the AppImage download it
  in the background and install on restart (**Restart to update** in the
  corner); the .deb and .rpm builds link to the release. Settings > About has
  Check now and an off switch. Releases now carry `latest*.yml` and
  `*.blockmap` files for this.
- Claude Code in the chat asks you instead of silently failing: a read outside
  the project (or, with the new **Allow edits and commands** setting, an edit or
  a command) shows an Allow / Allow for this chat / Deny card, and its
  questions (AskUserQuestion) show as cards with its options and a free-text
  answer. The chat trace shows its own tool calls and skills too.
- Your Claude Code skills, plugins and CLAUDE.md work in the chat (`/skill-name`,
  suggested as you type `/`); your hooks stay off. Settings > Advanced can
  switch this off. `/add-dir <folder>` lets it read another folder for the rest
  of the chat. Claude Code now works in the project folder, read-only by
  default (Read, Glob, Grep).
- Datasheets shows documents pulled in while researching in a collapsed
  Research section.
- The chat agent knows it is Ohmen, and what the app's tabs do.

### Fixed

- Long Ohmen tool calls on Claude Code (a design review) no longer hit the CLI's
  one-minute MCP timeout.

## [0.1.1] - 2026-10-24

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

[0.1.2]: https://github.com/R0mba-Na11avan/Ohmen/releases/tag/v0.1.2
[0.1.1]: https://github.com/R0mba-Na11avan/Ohmen/releases/tag/v0.1.1
[0.1.0]: https://github.com/R0mba-Na11avan/Ohmen/releases/tag/v0.1.0
