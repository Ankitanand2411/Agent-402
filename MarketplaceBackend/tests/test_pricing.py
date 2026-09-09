"""Exact decimal pricing. The first test is the bug that motivated the module."""

import pytest

from services.pricing import price_to_units, units_to_price

USDC = 6


def test_float_truncation_bug_is_fixed():
    # int(float("0.0157") * 1e6) == 15699 — one unit short of the advertised price.
    assert int(float("0.0157") * 1e6) == 15699
    assert price_to_units("0.0157", USDC) == 15700


@pytest.mark.parametrize(
    ("price", "units"),
    [("1", 1_000_000), ("0.5", 500_000), ("0.000001", 1), ("0", 0), ("12.345678", 12_345_678), (" 2.5 ", 2_500_000)],
)
def test_conversion_is_exact(price, units):
    assert price_to_units(price, USDC) == units


def test_accepts_numeric_types():
    assert price_to_units(0.0157, USDC) == 15700
    assert price_to_units(3, USDC) == 3_000_000


@pytest.mark.parametrize("bad", ["abc", "", "1e", "NaN", "Infinity", "-0.5", "0.0000001"])
def test_rejects_unrepresentable_prices(bad):
    with pytest.raises(ValueError):
        price_to_units(bad, USDC)


@pytest.mark.parametrize("price", ["0.0157", "1", "0.5", "12.345678", "0"])
def test_round_trip(price):
    assert units_to_price(price_to_units(price, USDC), USDC) == price
