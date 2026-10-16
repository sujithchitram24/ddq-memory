"""PDF extraction.

PDFs contain positioned glyphs and drawn lines, not "tables". Two libraries, two jobs:
- PyMuPDF (fitz): fast, robust open + encryption check + "is there a text layer?"
- pdfplumber: table detection from ruling lines and text alignment, plus text outside
  tables with coordinates, so we can keep reading order (title -> table -> footnote).
"""
import io
import logging
import re

import fitz  # PyMuPDF
import pdfplumber

from app.extraction import errors
from app.extraction.errors import ExtractionError
from app.extraction.ir import ExtractedDocument, Paragraph, Table
from app.extraction.tables import FOOTNOTE_LINE, clean, finalize

logging.getLogger("pdfminer").setLevel(logging.ERROR)  # silence "CropBox missing" noise

MIN_TEXT_CHARS = 20  # below this across the whole file we assume a scanned document


def _check_with_pymupdf(data: bytes) -> None:
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises various types for broken files
        raise ExtractionError(errors.CORRUPT_FILE, f"PDF could not be opened: {type(exc).__name__}") from exc
    with doc:
        if doc.needs_pass:
            raise ExtractionError(errors.ENCRYPTED, "PDF is password protected")
        if doc.page_count == 0:
            raise ExtractionError(errors.EMPTY_DOCUMENT, "PDF has no pages")
        chars = sum(len(page.get_text("text").strip()) for page in doc)
        if chars < MIN_TEXT_CHARS:
            raise ExtractionError(
                errors.NO_TEXT_LAYER,
                "PDF has no text layer (probably scanned). OCR is not implemented in this prototype.",
            )


def _lines_to_paragraphs(lines: list[dict]) -> list[tuple[float, str]]:
    """Merge text lines into paragraphs when the vertical gap is small."""
    paras: list[tuple[float, str]] = []
    cur: list[str] = []
    cur_top = prev_bottom = None
    for ln in lines:
        gap = None if prev_bottom is None else ln["top"] - prev_bottom
        # New paragraph on a big gap, or when a line looks like a footnote/table title.
        starts_new = gap is None or gap > 7 or FOOTNOTE_LINE.match(ln["text"]) or ln["text"].startswith("Table ")
        if starts_new and cur:
            paras.append((cur_top, " ".join(cur)))
            cur = []
        if not cur:
            cur_top = ln["top"]
        cur.append(ln["text"].strip())
        prev_bottom = ln["bottom"]
    if cur:
        paras.append((cur_top, " ".join(cur)))
    return paras


def parse_pdf(data: bytes) -> ExtractedDocument:
    _check_with_pymupdf(data)
    blocks: list[Paragraph | Table] = []
    warnings: list[str] = []
    table_index = 0
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page_no, page in enumerate(pdf.pages, start=1):
                found = page.find_tables()
                bboxes = [t.bbox for t in found]

                def outside(obj: dict, bboxes: list = bboxes) -> bool:
                    if obj.get("object_type") != "char":
                        return True
                    return not any(
                        x0 <= obj["x0"] and obj["x1"] <= x1 and top <= obj["top"] and obj["bottom"] <= bottom
                        for (x0, top, x1, bottom) in bboxes
                    )

                lines = page.filter(outside).extract_text_lines()
                items: list[tuple[float, Paragraph | Table]] = [
                    (top, Paragraph(text=text, location={"page": page_no}))
                    for top, text in _lines_to_paragraphs(lines)
                ]
                for t in found:
                    raw = [[clean(c) for c in row] for row in t.extract()]
                    raw = [r for r in raw if any(r)]
                    if len(raw) < 2:
                        warnings.append(f"page {page_no}: table with <2 rows skipped")
                        continue
                    header = [h or "" for h in raw[0]]
                    items.append(
                        (
                            t.bbox[1],
                            Table(
                                header=header,
                                rows=raw[1:],
                                location={"page": page_no, "table_index": table_index, "bbox": [round(v, 1) for v in t.bbox]},
                            ),
                        )
                    )
                    table_index += 1
                items.sort(key=lambda it: it[0])
                blocks.extend(b for _, b in items)
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(errors.CORRUPT_FILE, f"pdfplumber failed: {type(exc).__name__}") from exc

    return ExtractedDocument(file_format="pdf", parser="pymupdf+pdfplumber", blocks=attach_titles_and_footnotes(blocks), warnings=warnings)


TITLE = re.compile(r"^(Table\s+[\w.]+[:.]?\s*.*)$", re.IGNORECASE)


def attach_titles_and_footnotes(blocks: list[Paragraph | Table]) -> list[Paragraph | Table]:
    """Shared by PDF and DOCX: a short paragraph right before a table is its title; footnote-
    style paragraphs right after it are its footnotes. Both stay in the text too (the flat
    baseline must see exactly the same words)."""
    for i, b in enumerate(blocks):
        if not isinstance(b, Table):
            continue
        if b.title is None and i > 0 and isinstance(blocks[i - 1], Paragraph):
            prev = blocks[i - 1].text
            if TITLE.match(prev) or len(prev) < 80:
                b.title = prev
        j = i + 1
        while j < len(blocks) and isinstance(blocks[j], Paragraph) and FOOTNOTE_LINE.match(blocks[j].text):
            b.footnotes.append(blocks[j].text)
            j += 1
        finalize(b)
    return blocks
