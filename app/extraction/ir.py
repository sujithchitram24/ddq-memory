"""Intermediate representation (IR): what every parser must produce.

PDF, DOCX and XLSX are very different formats. Converting all of them into one shape
means everything downstream (chunking, fact normalisation, storage) is written once.
"""
from typing import Any, Literal

from pydantic import BaseModel, Field


class Paragraph(BaseModel):
    kind: Literal["paragraph"] = "paragraph"
    text: str
    location: dict[str, Any]  # e.g. {"page": 2} or {"sheet": "Notes", "cell": "A2"}


class Table(BaseModel):
    kind: Literal["table"] = "table"
    title: str | None = None
    header: list[str]
    rows: list[list[str | None]]
    footnotes: list[str] = Field(default_factory=list)
    location: dict[str, Any]
    quality_flags: list[str] = Field(default_factory=list)


Block = Paragraph | Table


class ExtractedDocument(BaseModel):
    file_format: Literal["pdf", "docx", "xlsx"]
    parser: str  # which library produced this, recorded as provenance
    blocks: list[Block]
    warnings: list[str] = Field(default_factory=list)

    @property
    def tables(self) -> list[Table]:
        return [b for b in self.blocks if isinstance(b, Table)]
