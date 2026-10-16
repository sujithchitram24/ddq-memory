"""Parse a table cell's text into a typed value.

We always keep value_raw (the exact text). value_numeric is a convenience for queries
and comparisons; if parsing fails we say so (validation_status='unparsed') rather than
guessing.
"""
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

CURRENCY_CODES = {"USD", "EUR", "GBP", "CHF", "JPY"}
CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP"}
MULTIPLIERS = {"k": 10**3, "m": 10**6, "mn": 10**6, "million": 10**6, "bn": 10**9, "billion": 10**9}
MISSING = {"n/a", "na", "-", "—", "nil", "not available", "n.a."}
HEADER_UNIT = re.compile(r"\((USD|EUR|GBP)\s*(k|m|mn|bn)?\)", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedValue:
    kind: str  # percent | money | number | year | text | empty
    numeric: Decimal | None
    unit: str | None
    currency: str | None
    status: str  # parsed | unparsed | missing


def _to_decimal(s: str) -> Decimal | None:
    s = s.replace(",", "").strip()
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return -d if negative else d


def parse_value(raw: str | None, column_label: str = "", row_label: str = "") -> ParsedValue:
    if raw is None or not raw.strip():
        return ParsedValue("empty", None, None, None, "missing")
    text = raw.strip()
    low = text.lower()
    if low in MISSING:
        return ParsedValue("empty", None, None, None, "missing")

    m = re.fullmatch(r"(\()?(-?[\d,]*\.?\d+)\)?\s*%", text)
    if m:
        num = _to_decimal(m.group(2))
        if m.group(1) and num is not None:  # accounting negative: (2.3)%
            num = -num
        return ParsedValue("percent", num, "percent", None, "parsed")

    m = re.fullmatch(r"(-?[\d,]*\.?\d+)\s*x", low)
    if m:
        return ParsedValue("number", _to_decimal(m.group(1)), "multiple", None, "parsed")

    # "USD 5,000,000", "EUR 2.5m", "$10m", "€ 750,000,000"
    m = re.fullmatch(r"(?:(?P<code>[A-Z]{3})|(?P<sym>[$€£]))\s*(?P<num>[\d,]*\.?\d+)\s*(?P<mult>k|m|mn|bn|million|billion)?", text, re.IGNORECASE)
    if m and (m.group("sym") or (m.group("code") or "").upper() in CURRENCY_CODES):
        cur = CURRENCY_SYMBOLS.get(m.group("sym") or "", (m.group("code") or "").upper())
        num = _to_decimal(m.group("num"))
        if num is not None and m.group("mult"):
            num *= MULTIPLIERS[m.group("mult").lower()]
        return ParsedValue("money", num, "currency", cur, "parsed")

    num = _to_decimal(text) if re.fullmatch(r"\(?-?[\d,]*\.?\d+\)?", text) else None
    if num is not None:
        # A unit in the column or row header, e.g. "Committed (USD m)".
        hm = HEADER_UNIT.search(column_label) or HEADER_UNIT.search(row_label)
        if hm:
            mult = MULTIPLIERS.get((hm.group(2) or "").lower(), 1)
            return ParsedValue("money", num * mult, "currency", hm.group(1).upper(), "parsed")
        if num == num.to_integral_value() and 1900 <= num <= 2100 and "vintage" in (column_label + row_label).lower():
            return ParsedValue("year", num, None, None, "parsed")
        return ParsedValue("number", num, None, None, "parsed")

    # Free text ("Committed capital", "Quarterly in advance") is a valid value, not an error.
    if re.search(r"\d", text):
        # Mixed text with numbers ("2% of total commitments") - keep as text, flag it.
        return ParsedValue("text", None, None, None, "unparsed")
    return ParsedValue("text", None, None, None, "parsed")


PERIOD_IN_TITLE = re.compile(r"as of ([0-9]{1,2} \w+ [0-9]{4}|\w+ [0-9]{4}|[0-9]{4}-[0-9]{2}-[0-9]{2})", re.IGNORECASE)
YEAR = re.compile(r"^(19|20)\d{2}$")


def detect_period(column_label: str, table_title: str | None) -> str | None:
    if YEAR.match(column_label.strip()):
        return column_label.strip()
    if table_title:
        m = PERIOD_IN_TITLE.search(table_title)
        if m:
            return f"as of {m.group(1)}"
    return None
