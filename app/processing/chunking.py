"""Turn an ExtractedDocument into text for retrieval.

Baseline A ("flat"): the whole document in reading order, tables rendered as markdown
rows (header row first, as a reasonable flattener would). Then split by word count.
That is a fair baseline: no information is removed; but once a table is split across
chunks, later rows lose their header.

"prose": the same, but tables left out (Approach B gets tables as structured facts).
"""
from dataclasses import dataclass
from typing import Any

from app.extraction.ir import ExtractedDocument, Paragraph, Table

CHUNK_SIZES = (50, 200)  # target words: S and L (chosen for a ~500-word corpus document)


@dataclass
class Unit:
    text: str
    location: dict[str, Any]


def _md_row(cells: list[str | None]) -> str:
    return "| " + " | ".join(c or "" for c in cells) + " |"


def linearize(doc: ExtractedDocument, *, include_tables: bool) -> list[Unit]:
    units: list[Unit] = []
    for b in doc.blocks:
        if isinstance(b, Paragraph):
            units.append(Unit(b.text, b.location))
        elif isinstance(b, Table) and include_tables:
            # Title is already a paragraph for PDF/DOCX; XLSX titles come from sheet names.
            if doc.file_format == "xlsx" and b.title:
                units.append(Unit(f"Sheet: {b.location.get('sheet')}", b.location))
            units.append(Unit(_md_row(b.header), b.location))
            for i, r in enumerate(b.rows):
                units.append(Unit(_md_row(r), {**_slim(b.location), "row": i}))
    return units


def _slim(loc: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in loc.items() if k not in ("row_numbers", "columns", "bbox")}


def chunk(units: list[Unit], size: int) -> list[tuple[str, dict[str, Any]]]:
    """Greedy: add whole lines until the chunk reaches `size` words. A single line longer
    than `size` is split by words. No overlap (kept simple and documented)."""
    chunks: list[tuple[str, dict[str, Any]]] = []
    cur: list[str] = []
    cur_words = 0
    first_loc: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal cur, cur_words, first_loc
        if cur:
            chunks.append(("\n".join(cur), {"start": _slim(first_loc or {}), "lines": len(cur)}))
        cur, cur_words, first_loc = [], 0, None

    for u in units:
        words = u.text.split()
        while len(words) > size:  # oversize line
            flush()
            chunks.append((" ".join(words[:size]), {"start": _slim(u.location), "lines": 1}))
            words = words[size:]
        if cur_words + len(words) > size:
            flush()
        if first_loc is None:
            first_loc = u.location
        cur.append(" ".join(words))
        cur_words += len(words)
    flush()
    return chunks
