"""Tests for the building-level address normalization helper.

Pure string function — no warehouse, no I/O. We assert that formatting variants
of one address collapse to a single key, distinct buildings stay distinct, and
unkeyable inputs return ``None``.
"""

from __future__ import annotations

from relief_probe.detectors._address import normalize_address


def test_equivalence_class_collapses_to_one_key():
    a = normalize_address("123 Main Street", "Austin", "TX", "78701")
    b = normalize_address("123 MAIN ST", "Austin", "TX", "78701")
    c = normalize_address("123 Main St., Suite 200", "Austin", "TX", "78701")
    assert a is not None
    assert a == b == c


def test_unit_designator_variants_collapse():
    base = normalize_address("500 Congress Ave", "Austin", "TX", "78701")
    variants = (
        "500 Congress Ave Ste 100",
        "500 Congress Ave #12",
        "500 Congress Ave Unit B",
    )
    for unit in variants:
        assert normalize_address(unit, "Austin", "TX", "78701") == base


def test_suffix_abbreviations_all_normalize():
    pairs = [
        ("1 Oak Boulevard", "1 Oak Blvd"),
        ("2 Elm Road", "2 Elm Rd"),
        ("3 Pine Drive", "3 Pine Dr"),
        ("4 Cedar Lane", "4 Cedar Ln"),
        ("5 Birch Court", "5 Birch Ct"),
        ("6 Maple Place", "6 Maple Pl"),
        ("7 First Avenue", "7 First Ave"),
    ]
    for spelled, abbrev in pairs:
        assert normalize_address(spelled, "Dallas", "TX") == normalize_address(
            abbrev, "Dallas", "TX"
        )


def test_distinct_buildings_map_to_distinct_keys():
    a = normalize_address("123 Main St", "Austin", "TX", "78701")
    b = normalize_address("125 Main St", "Austin", "TX", "78701")  # different number
    c = normalize_address("123 Main St", "Dallas", "TX", "75201")  # different city/zip
    assert len({a, b, c}) == 3


def test_blank_and_none_return_none():
    assert normalize_address(None) is None
    assert normalize_address("") is None
    assert normalize_address("   ") is None
    # Only punctuation/units survive to nothing → unkeyable.
    assert normalize_address(".,#", "Austin", "TX") is None


def test_is_pure_no_side_effects():
    addr = "123 Main Street"
    first = normalize_address(addr, "Austin", "TX", "78701")
    second = normalize_address(addr, "Austin", "TX", "78701")
    assert first == second
    assert addr == "123 Main Street"  # input not mutated
