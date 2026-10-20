# Contributing to Ohmen

Thanks for helping. Bug reports, review patterns for new parts, fixes and docs
are all welcome. For anything larger than a small fix, open an issue first so we
can agree on the approach before you spend time on it.

By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Ways to help

- **Report a bug** with the issue template. If the app misbehaves, attach the
  sidecar log (Settings › About shows its path).
- **Report a bad finding.** A false alarm or a missed bug is the most useful
  feedback there is. Include the part, the finding, and what the datasheet
  actually says.
- **Add a review pattern** for a part you know well. See
  [docs/patterns.md](docs/patterns.md). It's YAML, with no Python needed.
- **macOS support.** The code is cross-platform, but nobody on the project can
  build or test on a Mac yet.

## Running from source

You need Python 3.11+ (3.12 recommended), Node.js 20+, and KiCad 9+.

```bash
git clone https://github.com/R0mba-Na11avan/Ohmen.git
cd Ohmen

# Python side (the review engine + local API the app talks to)
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev,server]"

# Desktop app
cd app
npm install
npm run dev                          # opens the app and starts the Python sidecar
```

In development the app runs the sidecar with `python` (Windows) or `python3`
(Linux) from your shell. Set `PCB_PYTHON` to use a different interpreter, such
as the venv above if it isn't activated.

For development you can put API keys in a `.env` file at the repo root (copy
`.env.example`) instead of entering them in Settings.

## Project layout

```
src/pcb_assistant/   Python engine: KiCad adapter, typed design IR, review
                     patterns, providers, agent, local API server
app/                 Electron + React desktop app
tests/               pytest suite (offline: no network, no API keys)
evals/               paid, manual model evals (not run in CI)
packaging/           PyInstaller spec + pinned dependencies for installers
docs/                architecture, pattern format, design system
```

[docs/architecture.md](docs/architecture.md) explains how the pieces fit.

## Checks before a pull request

```bash
pytest                               # from the repo root
cd app && npm run typecheck
```

If you change a Python model that the app uses (IR, findings, BOM), regenerate
the TypeScript types and commit them. CI fails if they drift:

```bash
cd app && npm run gen-types
```

Keep pull requests focused: one change per PR, with a short description of what
and why. Match the style of the code around your change.

## Building the installers locally

```bash
pip install -r packaging/requirements.lock
pip install --no-deps .
python -m PyInstaller packaging/sidecar.spec --noconfirm --distpath packaging/dist --workpath packaging/build
cd app && npm run dist               # output in app/release/
```

Releases are built by GitHub Actions when a `v*` tag is pushed
(`.github/workflows/release.yml`).

## Updating Python dependencies

Ranges live in `pyproject.toml`; installers and CI use the exact versions in
`packaging/requirements.lock`. After changing a dependency, regenerate the lock:

```bash
pip install uv
uv pip compile pyproject.toml --extra server --extra packaging --universal --python-version 3.12 -o packaging/requirements.lock
```

## Datasheets and copyright

Don't commit datasheet PDFs or long passages copied from them; vendors hold the
copyright. Patterns cite datasheets by section and table, with a short
paraphrase of the rule.
