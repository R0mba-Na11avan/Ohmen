"""On-demand LLM eval for Loop B (schematic review). COSTS API MONEY.

Pass bar, run against the real modules:
  * floating-EN board  -> catches it, cites the specific spec
  * correct board      -> does NOT false-flag EN as must-fix
across each model. Run manually before a release, not in CI.

    python evals/eval_loop_b.py                     # default models
    python evals/eval_loop_b.py claude:claude-sonnet-5 gemini:gemini-3.8-flash

The LMR51625 datasheet is untracked (vendor copyright); it is fetched on demand.
"""
import sys
from pathlib import Path

from pcb_assistant.ir.models import Design
from pcb_assistant.reasoning.loop_b_review import review
from pcb_assistant.reasoning.retrieval import fetch_if_missing

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
PDF = ROOT / "evals" / ".cache" / "lmr51625.pdf"
TI_URL = "https://www.ti.com/lit/gpn/lmr51625"

DEFAULT_MODELS = ["claude:claude-sonnet-5", "gemini:gemini-3.8-flash"]
CITE_TOKENS = ("table", "§", "section", "pin", "abs", "enable")


def _design(name: str) -> Design:
    return Design.model_validate_json((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _scores(text: str) -> dict:
    low = text.lower()
    return {
        "flags_en": ("en" in low and ("float" in low or "enable" in low)),
        "cites": any(t in low for t in CITE_TOKENS),
        "must_fix": "must-fix" in low or "must fix" in low,
    }


def run(models: list[str]) -> int:
    fetch_if_missing(TI_URL, PDF)
    correct, floating = _design("ir_correct"), _design("ir_floating_en")
    failures = 0

    for spec in models:
        print(f"\n===== {spec} =====")
        f_review = review(floating, spec, datasheet_pdf=PDF, focus="LMR51625")
        c_review = review(correct, spec, datasheet_pdf=PDF, focus="LMR51625")
        fs, cs = _scores(f_review), _scores(c_review)

        catch = fs["flags_en"] and fs["cites"]
        control_clean = not (cs["flags_en"] and cs["must_fix"])
        print(f"  floating: catches+cites EN = {catch}  ({fs})")
        print(f"  correct : no false must-fix EN = {control_clean}  ({cs})")
        verdict = "PASS" if (catch and control_clean) else "FAIL"
        print(f"  -> {verdict}")
        failures += verdict == "FAIL"

    print(f"\n{len(models) - failures}/{len(models)} models passed.")
    return failures


if __name__ == "__main__":
    models = sys.argv[1:] or DEFAULT_MODELS
    raise SystemExit(1 if run(models) else 0)
