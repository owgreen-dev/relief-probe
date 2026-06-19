"""Tests for the entity-resolution key helper.

Pure function — no warehouse, no I/O. We assert that the same borrower at one
building collapses across name/address formatting variants, that different
borrowers or different buildings stay distinct, and that an unkeyable name or
address yields ``None``.
"""

from __future__ import annotations

from relief_probe.detectors._entity import entity_key


def test_same_borrower_same_building_variants_collapse():
    a = entity_key("Acme LLC", "1 Main St", "Austin", "TX", "78701")
    b = entity_key("ACME", "1 MAIN STREET", "Austin", "TX", "78701")
    c = entity_key("The Acme Inc.", "1 Main Street, Suite 200", "Austin", "TX", "78701")
    assert a is not None
    assert a == b == c


def test_different_borrower_same_building_differs():
    a = entity_key("Acme LLC", "1 Main St", "Austin", "TX", "78701")
    b = entity_key("Beta Corp", "1 Main St", "Austin", "TX", "78701")
    assert a != b


def test_same_borrower_different_building_differs():
    a = entity_key("Acme LLC", "1 Main St", "Austin", "TX", "78701")
    b = entity_key("Acme LLC", "999 Oak Ave", "Dallas", "TX", "75201")
    assert a != b


def test_blank_name_returns_none():
    assert entity_key("", "1 Main St", "Austin", "TX", "78701") is None
    assert entity_key(None, "1 Main St", "Austin", "TX", "78701") is None
    # Name that normalizes to nothing (only corporate suffixes) is unkeyable.
    assert entity_key("LLC Inc", "1 Main St", "Austin", "TX", "78701") is None


def test_blank_address_returns_none():
    assert entity_key("Acme LLC", None, "Austin", "TX", "78701") is None
    assert entity_key("Acme LLC", "", "Austin", "TX", "78701") is None
    assert entity_key("Acme LLC", "   ", "Austin", "TX", "78701") is None


def test_is_pure_no_side_effects():
    name, addr = "Acme LLC", "1 Main Street"
    first = entity_key(name, addr, "Austin", "TX", "78701")
    second = entity_key(name, addr, "Austin", "TX", "78701")
    assert first == second
    assert name == "Acme LLC"  # inputs not mutated
    assert addr == "1 Main Street"
