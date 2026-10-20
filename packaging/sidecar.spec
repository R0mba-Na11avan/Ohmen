# PyInstaller spec: freezes the Python sidecar into packaging/dist/sidecar/, which
# electron-builder ships as resources/sidecar/ (see app/electron-builder.yml).
#
#   pyinstaller packaging/sidecar.spec --noconfirm --distpath packaging/dist --workpath packaging/build
#
# One-folder, not one-file: a one-file build re-launches itself as a child
# process, which outlives the app when Electron kills the parent on Windows.
from pathlib import Path

from importlib.metadata import PackageNotFoundError

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"

hiddenimports = (
    collect_submodules("pcb_assistant")
    # uvicorn picks its loop / protocol / lifespan implementations by name.
    + collect_submodules("uvicorn")
    # pydantic-ai imports this lazily when it prices a reply.
    + collect_submodules("genai_prices")
)

# The pattern library is data the runner globs at runtime.
datas = [(str(SRC / "pcb_assistant" / "patterns" / "library" / "*.yaml"), "pcb_assistant/patterns/library")]

# These packages read their own version with importlib.metadata at import time,
# which fails ("No package metadata was found for ...") unless the dist-info ships too.
for dist in (
    "genai_prices", "pydantic_ai_slim", "pydantic_graph", "google_genai",
    "openai", "anthropic", "opentelemetry_api", "logfire_api", "aiohttp",
):
    try:
        datas += copy_metadata(dist)
    except PackageNotFoundError:
        pass

a = Analysis(
    [str(ROOT / "packaging" / "sidecar_entry.py")],
    pathex=[str(SRC)],
    hiddenimports=hiddenimports,
    datas=datas,
    excludes=["tkinter", "pytest", "_pytest", "PyInstaller"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ohmen-sidecar",
    console=True,  # Electron hides the window and captures the output to a log
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="sidecar", upx=False)
