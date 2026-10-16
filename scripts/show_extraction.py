"""Debug helper: print what the parser extracted from a file.

Usage: uv run python -m scripts.show_extraction corpus/northwind_growth_iii.pdf
"""
import sys
from pathlib import Path

from app.extraction import extract
from app.extraction.ir import Table

path = Path(sys.argv[1])
doc = extract(path.suffix.lstrip(".").lower(), path.read_bytes())
print(f"parser={doc.parser} blocks={len(doc.blocks)} warnings={doc.warnings}")
for b in doc.blocks:
    if isinstance(b, Table):
        print(f"\n[TABLE] title={b.title!r} location={b.location} flags={b.quality_flags}")
        print("  header:", b.header)
        for r in b.rows:
            print("  row:   ", r)
        for f in b.footnotes:
            print("  note:  ", f)
    else:
        print(f"[PARA]  {b.text[:110]!r}  {b.location}")
