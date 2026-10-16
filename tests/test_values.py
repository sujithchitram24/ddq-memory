from decimal import Decimal

import pytest

from app.processing.values import detect_period, parse_value


@pytest.mark.parametrize(
    "raw,kind,numeric,currency",
    [
        ("1.5%", "percent", Decimal("1.5"), None),
        ("(2.3)%", "percent", Decimal("-2.3"), None),
        ("USD 5,000,000", "money", Decimal("5000000"), "USD"),
        ("EUR 2.5m", "money", Decimal("2500000.0"), "EUR"),
        ("$10m", "money", Decimal("10000000"), "USD"),
        ("1.6x", "number", Decimal("1.6"), None),
        ("Committed capital", "text", None, None),
    ],
)
def test_parse_value(raw, kind, numeric, currency):
    v = parse_value(raw)
    assert (v.kind, v.numeric, v.currency) == (kind, numeric, currency)


def test_unit_from_header():
    v = parse_value("250", column_label="Committed (USD m)")
    assert v.kind == "money" and v.currency == "USD" and v.numeric == Decimal("250000000")


@pytest.mark.parametrize("raw", [None, "", "n/a", "-"])
def test_missing_values_are_marked_missing_not_zero(raw):
    v = parse_value(raw)
    assert v.status == "missing" and v.numeric is None


def test_text_with_numbers_is_flagged_unparsed():
    assert parse_value("2% of total commitments").status == "unparsed"


def test_period_detection():
    assert detect_period("2024", None) == "2024"
    assert detect_period("Net IRR", "Prior Fund Performance (as of 31 December 2025)") == "as of 31 December 2025"
