"""The core claim of the project depends on these: each value stays attached to its
row label and column header, for every file format."""
import pytest

from app.extraction import extract
from app.extraction.errors import ExtractionError
from tests.conftest import CORPUS, FIXTURES


def cell(doc, title_contains: str, row_label: str, column: str):
    for t in doc.tables:
        if title_contains.lower() in (t.title or "").lower():
            col = t.header.index(column)
            for r in t.rows:
                if r[0] and r[0].startswith(row_label):
                    return r[col]
    raise AssertionError(f"not found: {title_contains}/{row_label}/{column}")


def load(name):
    p = CORPUS / name
    return extract(p.suffix[1:], p.read_bytes())


def test_pdf_fee_table_keeps_headers():
    doc = load("northwind_growth_iii.pdf")
    assert cell(doc, "Fee Terms", "Management fee (Investment Period)", "Basis") == "Committed capital"
    assert cell(doc, "Fee Terms", "Management fee (post-Investment Period)", "Rate") == "1.5%"
    assert cell(doc, "Prior Fund Performance", "Northwind Growth Partners II", "Net IRR") == "14.2%"


def test_pdf_footnote_attached_to_table():
    doc = load("northwind_growth_iii.pdf")
    fees = next(t for t in doc.tables if "Fee Terms" in (t.title or ""))
    assert any("0.25%" in f for f in fees.footnotes)


def test_docx_fee_table_and_merged_two_row_header():
    doc = load("harbor_lane_credit_ii.docx")
    assert cell(doc, "Table B", "Management fee", "Calculated on") == "Invested capital"
    stats = next(t for t in doc.tables if "Portfolio" in (t.title or ""))
    assert "multi_row_header" in stats.quality_flags
    assert cell(doc, "Portfolio", "Weighted average yield", "Vintage fund Fund II") == "9.8%"


def test_xlsx_percent_format_multi_sheet_and_formula_flag():
    doc = load("summit_ridge_infra_i.xlsx")
    assert cell(doc, "Fee Schedule", "Management fee", "Rate") == "1.25%"  # stored as 0.0125
    assert cell(doc, "Performance", "Net IRR", "2024") == "9.6%"
    terms = next(t for t in doc.tables if t.location["sheet"] == "Terms")
    assert "formula_without_cached_value" in terms.quality_flags
    assert any("B4" in w for w in doc.warnings)


def test_xlsx_missing_header_is_flagged_not_guessed():
    doc = extract("xlsx", (FIXTURES / "no_header.xlsx").read_bytes())
    assert "missing_header" in doc.tables[0].quality_flags


@pytest.mark.parametrize("name,code", [("scanned.pdf", "NO_TEXT_LAYER"), ("corrupt.pdf", "CORRUPT_FILE")])
def test_pdf_failures_are_typed_and_permanent(name, code):
    with pytest.raises(ExtractionError) as e:
        extract("pdf", (FIXTURES / name).read_bytes())
    assert e.value.code == code and e.value.retryable is False
