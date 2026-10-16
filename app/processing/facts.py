"""Approach B: turn each extracted table into typed facts with provenance."""
import re
from dataclasses import dataclass, field
from typing import Any

from app.extraction.ir import Table
from app.processing.values import detect_period, parse_value

MARKER = re.compile(r"(\(\d+\)|[¹²³⁴⁵⁶⁷⁸⁹†‡]|\*)")


@dataclass
class FactDraft:
    row_key: str
    table_title: str | None
    row_label: str
    column_label: str
    value_raw: str
    value_numeric: Any
    value_kind: str
    unit: str | None
    currency: str | None
    period: str | None
    footnotes: list[str]
    location: dict[str, Any]
    extraction_method: str
    validation_status: str
    search_text: str = field(default="")


def _footnotes_for(texts: list[str], footnotes: list[str]) -> list[str]:
    markers = {m for t in texts for m in MARKER.findall(t or "")}
    return [f for f in footnotes if any(f.strip().startswith(m) for m in markers)]


def _cell_location(table: Table, row_idx: int, col_idx: int, parser: str) -> dict[str, Any]:
    loc = {k: v for k, v in table.location.items() if k not in ("row_numbers", "columns", "bbox")}
    loc.update({"row": row_idx, "col": col_idx})
    if "row_numbers" in table.location:  # XLSX: exact cell reference, e.g. Fees!B3
        loc["cell"] = f"{table.location['columns'][col_idx]}{table.location['row_numbers'][row_idx]}"
    return loc


def table_to_facts(table: Table, table_seq: int, parser: str) -> list[FactDraft]:
    facts: list[FactDraft] = []
    for r_idx, row in enumerate(table.rows):
        row_label = (row[0] or "").strip()
        if not row_label:
            continue  # a value without a row label cannot be described; not stored as a fact
        row_notes = _footnotes_for(row, table.footnotes)
        for c_idx in range(1, len(table.header)):
            column_label = table.header[c_idx] or f"column_{c_idx + 1}"
            raw = row[c_idx] if c_idx < len(row) else None
            pv = parse_value(raw, column_label, row_label)
            clean_label = MARKER.sub("", row_label).strip()
            f = FactDraft(
                row_key=f"{table_seq}:{r_idx}",
                table_title=table.title,
                row_label=clean_label,
                column_label=column_label,
                value_raw=raw or "",
                value_numeric=pv.numeric,
                value_kind=pv.kind,
                unit=pv.unit,
                currency=pv.currency,
                period=detect_period(column_label, table.title),
                footnotes=row_notes,
                location=_cell_location(table, r_idx, c_idx, parser),
                extraction_method=f"{parser}/table",
                validation_status=pv.status,
            )
            f.search_text = " ".join(
                p for p in [table.title, clean_label, column_label, raw, f.period, *row_notes] if p
            )
            facts.append(f)
    return facts


def render_row(facts: list[Any]) -> str:
    """One readable line per table row: the 'evidence card' Approach B retrieves.
    Every value is printed next to its column label, and the row label and table title
    are always present - this is the whole point of the hypothesis."""
    first = facts[0]
    parts = []
    for f in facts:
        value = f.value_raw or "(empty)"
        parts.append(f"{f.column_label} = {value}")
    line = f"[{first.table_title or 'Table'}] {first.row_label}: " + "; ".join(parts)
    period = next((f.period for f in facts if f.period and not f.period.isdigit()), None)
    if period:
        line += f" ({period})"
    notes = sorted({n for f in facts for n in f.footnotes})
    if notes:
        line += " Notes: " + " ".join(notes)
    return line
