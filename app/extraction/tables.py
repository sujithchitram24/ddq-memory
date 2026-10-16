"""Shared table clean-up and quality checks, used by all three parsers.

Parser output is NOT ground truth. These checks flag tables that look wrong so that a
human (or a stricter pipeline) can review them instead of trusting them silently.
"""
import re

from app.extraction.ir import Table

FOOTNOTE_LINE = re.compile(r"^\s*(\(\d+\)|[¹²³⁴⁵⁶⁷⁸⁹*†‡]|\d\))\s*\S")


def clean(value: object) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def combine_header_rows(top: list[str | None], bottom: list[str | None]) -> list[str]:
    """Two-row header (e.g. a merged 'Vintage fund' over 'Fund I' | 'Fund II')."""
    out = []
    for t, b in zip(top, bottom):
        parts = [p for p in (t, b) if p]
        # Avoid "Metric Metric" when a vertically merged cell repeats.
        out.append(" ".join(dict.fromkeys(parts)) if parts else "")
    return out


def quality_flags(table: Table) -> list[str]:
    flags: list[str] = []
    width = len(table.header)
    if any(not h for h in table.header):
        flags.append("empty_header_cell")
    if len(set(h.lower() for h in table.header if h)) < len([h for h in table.header if h]):
        flags.append("duplicate_header")
    if any(len(r) != width for r in table.rows):
        flags.append("ragged_rows")
    if not table.rows:
        flags.append("no_data_rows")
    if table.rows and all(r and r[0] is None for r in table.rows):
        flags.append("missing_row_labels")
    numericish = re.compile(r"^[\d.,%x()$€£ -]+$")
    if table.header and all(numericish.match(h or "x") for h in table.header):
        flags.append("header_looks_like_data")
    return flags


def finalize(table: Table) -> Table:
    # Flag first (so ragged rows are recorded), then pad/trim rows to the header width.
    table.quality_flags = sorted(set(table.quality_flags + quality_flags(table)))
    width = len(table.header)
    table.rows = [(list(r) + [None] * width)[:width] for r in table.rows]
    return table
