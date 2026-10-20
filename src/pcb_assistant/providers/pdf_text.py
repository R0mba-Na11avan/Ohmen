"""Datasheet-as-text for models with no native PDF input (OpenAI, local, Codex).

Tables and pinout drawings lose their layout in extraction, so the prompt says
the text is extracted — a weaker grounding than a native PDF, but better than
refusing the call outright.
"""
from __future__ import annotations

from pcb_assistant.datasheets.text import extract_pages
from pcb_assistant.datasheets.tools import strip_running_lines

MAX_CHARS = 120_000  # ~30k tokens: a long datasheet's key sections, not all 200 pages


def pdf_as_text(pdf: bytes, max_chars: int = MAX_CHARS) -> str:
    pages = strip_running_lines(extract_pages(pdf))
    out, used = [], 0
    for n, text in enumerate(pages, 1):
        block = f"=== page {n} of {len(pages)} ===\n{text.strip()}"
        if used + len(block) > max_chars:
            out.append(f"=== pages {n}–{len(pages)} omitted (length) ===")
            break
        out.append(block)
        used += len(block)
    return "\n\n".join(out)


def inline_pdf(prompt: str, pdf: bytes) -> str:
    """`prompt` preceded by the datasheet's extracted text."""
    return (
        "The datasheet is attached below as extracted text (tables and figures may have "
        "lost their layout; say so if a value is ambiguous). Cite pages as 'p.N'.\n\n"
        f"<datasheet>\n{pdf_as_text(pdf)}\n</datasheet>\n\n{prompt}"
    )
