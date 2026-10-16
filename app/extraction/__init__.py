"""Format dispatch: one entry point for all parsers."""
from app.extraction.docx_parser import parse_docx
from app.extraction.ir import ExtractedDocument
from app.extraction.pdf_parser import parse_pdf
from app.extraction.xlsx_parser import parse_xlsx

PARSERS = {"pdf": parse_pdf, "docx": parse_docx, "xlsx": parse_xlsx}


def extract(file_format: str, data: bytes) -> ExtractedDocument:
    return PARSERS[file_format](data)
