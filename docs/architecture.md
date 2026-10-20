# Architecture

Ohmen is two programs: an Electron desktop app, and a Python "sidecar"
that does the actual work. The app starts the sidecar on launch, talks to it over
HTTP and WebSocket on `127.0.0.1`, and stops it on quit.

```
 KiCad project folder                Python sidecar (src/pcb_assistant)
 ┌────────────────────┐   kicad-cli   ┌───────────────────────────────────────────┐
 │ .kicad_pro         │ ────────────▶ │ adapters/   netlist → Design IR           │
 │ .kicad_sch / _pcb  │   (read only) │ ir/         typed, tool-neutral IR        │
 └────────────────────┘               │ patterns/   deterministic YAML rules      │
                                      │ reasoning/  LLM review team, parts, chat  │
 Electron app (app/)                  │ verify/     citation + connectivity gate  │
 ┌────────────────────┐  HTTP + WS    │ datasheets/ fetch, text, page search      │
 │ main: spawns +     │ ◀───────────▶ │ agent/      tool-using copilot            │
 │   supervises       │  per-launch   │ bom/, distributors/  BOM + DigiKey        │
 │ renderer: React UI │  token        │ providers/, engines/ model access         │
 └────────────────────┘               │ server/     FastAPI app                   │
                                      └───────────────────────────────────────────┘
```

## Two boundaries

The code is organised around two boundaries, so that adding an EDA tool or a
model provider doesn't touch the reasoning code.

**The Design IR** (`ir/models.py`) is a typed, tool-neutral description of a
board: components with their fields, nets, and the pins on each net. Only
adapters know a tool's file format. `adapters/kicad_netlist.py` runs
`kicad-cli sch export netlist` and parses the result; supporting another EDA tool
means writing another adapter that produces the same IR. The IR's JSON Schema is
also the source of the app's TypeScript types (`app/scripts/gen-types.mjs`).

**The provider shim** (`providers/`) puts every model behind one interface, with
per-model capability metadata (tool use, native PDF input, context size). The
reasoning code asks for a capability, never for a vendor. The tool-using agent
uses [pydantic-ai](https://ai.pydantic.dev/) on top of the same credentials.
`engines/` adds an alternative route through the Codex or Claude Code CLIs,
which bridge back to the app's tools over a local MCP server.

## How a review works

1. **Ingest.** The project's root schematic is exported to a netlist and parsed
   into the IR. Nothing is written to the KiCad project.
2. **Free checks.** KiCad's own ERC/DRC (`adapters/kicad_checks.py`) plus the
   pattern library (`patterns/`): YAML rules matched to parts and evaluated
   against the netlist. Their findings are verified by construction.
3. **AI review.** `reasoning/review_team.py` splits the board across reviewers
   (one at *low* depth, up to six at *high*). Each reviewer
   (`reasoning/reviewer_agent.py`) audits its parts with tools: netlist lookups,
   datasheet search and page reads, arithmetic for dividers and limits.
4. **Evidence gate.** A reviewer can only report through a tool that checks
   its evidence (`report_finding` in `reviewer_agent.py`). A wrong claim about
   a connection is refused with the real one, so the reviewer corrects itself.
   A datasheet quote must appear on a page the reviewer actually opened. A
   must-fix also has to say what was ruled out, and gets an independent second
   opinion; if that rejects it, it drops to *verify*. Finally the citation gate
   (`verify/verifier.py`) downgrades any uncited must-fix to *verify* and labels
   it unverified. Nothing is hidden: the model may, and should, say it couldn't
   verify something.
5. **Merge.** Findings from all sources are de-duplicated and ranked
   (must-fix / verify / cosmetic). Dismissals are stored per project.

## Where things are stored

| What | Where |
|---|---|
| API keys, app settings | Electron's user-data folder (`%APPDATA%\Ohmen` on Windows, `~/.config/Ohmen` on Linux). Keys are encrypted with `safeStorage`. |
| Chats, BOM, findings, dismissals, caches | `<project>/.pcb_assistant/` |
| Datasheet PDFs | `<project>/datasheets/` |
| Sidecar log (installed app) | Electron's logs folder; shown in Settings › About |

## Security model

The sidecar holds the user's API keys, and any web page can try to reach
`127.0.0.1`. So the app generates a random token on each launch and passes it
to the sidecar (`PCB_SIDECAR_TOKEN`). Every request must carry it, and must name
a loopback `Host`, which blocks DNS rebinding (`server/auth.py`). Keys go from
the main process to the sidecar and never back to the renderer.

## Packaging

Installers bundle a frozen sidecar, so users need neither Python nor Node.
`packaging/sidecar.spec` freezes the sidecar with PyInstaller (one-folder mode)
from the pinned `packaging/requirements.lock`. `app/electron-builder.yml` copies
it into the app's `resources/sidecar/`, and the main process
(`app/src/main/index.ts`) launches it from there when `app.isPackaged`. In
development the main process runs `python -m pcb_assistant.server` instead.
