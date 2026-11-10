"""Closest-offering matching tests. All fixtures are synthetic."""

from __future__ import annotations

from decimal import Decimal

from conductor.agent.offering_match import select_offering

OFFERINGS = [
    {"id": "small", "name": "Small Instance", "cpu_count": 1, "memory_gib": "0.5"},
    {"id": "c3-small", "name": "c3.small", "cpu_count": 2, "memory_gib": "4"},
    {"id": "c3-medium", "name": "c3.medium", "cpu_count": 4, "memory_gib": "8"},
    {"id": "g3-small", "name": "g3.small", "cpu_count": 2, "memory_gib": "8"},
    {"id": "m3-small", "name": "m3.small", "cpu_count": 2, "memory_gib": "16"},
]


def test_exact_shape_chooses_exact_offering() -> None:
    match = select_offering(OFFERINGS, cpu=2, memory=Decimal("4"))
    assert match is not None
    assert match.offering["name"] == "c3.small"
    assert match.exact
    assert match.satisfies


def test_closest_shape_is_selected_and_described() -> None:
    match = select_offering(OFFERINGS, cpu=2, memory=Decimal("9"))
    assert match is not None
    assert match.offering["name"] == "g3.small"
    assert not match.exact
    text = match.describe(locale="en")
    assert "closest available" in text
    assert "does not fully meet" in text


def test_undersized_offering_is_reported_as_not_satisfying() -> None:
    match = select_offering([OFFERINGS[0]], cpu=2, memory=Decimal("4"))
    assert match is not None
    assert not match.exact
    assert not match.satisfies
    assert "低于请求" in match.describe()


def test_explicit_name_wins_over_shape() -> None:
    match = select_offering(OFFERINGS, hint="c3.medium", cpu=2, memory=Decimal("4"))
    assert match is not None
    assert match.offering["name"] == "c3.medium"


def test_no_hint_and_multiple_offerings_does_not_guess() -> None:
    assert select_offering(OFFERINGS) is None


def test_single_offering_is_a_safe_default() -> None:
    match = select_offering([OFFERINGS[0]])
    assert match is not None
    assert match.offering["name"] == "Small Instance"


def test_tie_prefers_a_shape_that_meets_the_request() -> None:
    match = select_offering(OFFERINGS, cpu=3, memory=Decimal("6"))
    assert match is not None
    assert match.offering["name"] == "c3.medium"
    assert match.satisfies
