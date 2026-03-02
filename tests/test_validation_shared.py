import pytest

from src.validation import normalize_optional_iso_date, parse_iso_date, validate_date_window


def test_parse_iso_date_accepts_valid_date():
    assert parse_iso_date("2024-01-31") == "2024-01-31"


def test_parse_iso_date_rejects_invalid_format():
    with pytest.raises(ValueError):
        parse_iso_date("2024/01/31")


def test_normalize_optional_iso_date_strips_and_handles_empty():
    assert normalize_optional_iso_date(" 2024-02-01 ") == "2024-02-01"
    assert normalize_optional_iso_date("   ") is None
    assert normalize_optional_iso_date(None) is None


def test_validate_date_window_rejects_inverted_range():
    with pytest.raises(ValueError):
        validate_date_window("2024-12-31", "2024-01-01")
