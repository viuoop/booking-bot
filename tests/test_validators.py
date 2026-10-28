import pytest

from validators import check_text, parse_hours, parse_phone, parse_price


def test_parse_hours():
    assert parse_hours("10:00-19:00") == ("10:00", "19:00")
    assert parse_hours("9:00 - 18:30") == ("09:00", "18:30")
    for bad in ("19:00-10:00", "25:00-26:00", "10:00-10:00", "abc"):
        assert parse_hours(bad) is None


def test_parse_price():
    assert parse_price("1500") == 1500
    assert parse_price("1 500") == 1500
    assert parse_price("0") == 0
    for bad in ("-5", "abc", "2000000", "", "١٢٣"):
        assert parse_price(bad) is None


def test_parse_phone():
    assert parse_phone("+380 (50) 123-45-67") == "+380501234567"
    assert parse_phone("0501234567") == "0501234567"
    for bad in ("123", "abc", "+38050123456789012", "٠٥٠١٢٣٤٥٦٧"):
        assert parse_phone(bad) is None


def test_check_text():
    assert check_text("  Анна ") == "Анна"
    assert check_text("a" * 100) == "a" * 100
    for bad in (None, "", "   ", "a" * 101):
        assert check_text(bad) is None
