"""
Price ↔ atomic-unit conversion.

Tool prices are stored as decimal strings ("0.0157") and paid in USDC, which
has 6 decimals, so the on-chain amount is an integer number of "atomic units"
(1 USDC = 1_000_000 units).

The original conversion was `int(float(price) * 1e6)`. Binary floats cannot
represent most decimal fractions exactly: 0.0157 * 1e6 evaluates to
15699.999999999998 and `int()` truncates it to 15699, so the server demanded
one unit less than the advertised price. `decimal.Decimal` works in base 10
and does not have that problem.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def price_to_units(price: str | int | float | Decimal, decimals: int) -> int:
    """
    Convert a human price to atomic units, exactly.

    Raises ValueError if the price is not a number, is negative, or carries
    more fractional digits than the token supports (such a price cannot be
    settled exactly on-chain, so it is a configuration error, not something
    to round silently).
    """
    if isinstance(price, float):
        # Route floats through str() so 0.0157 becomes Decimal("0.0157"),
        # not Decimal(0.015699999999999998...).
        price = str(price)
    try:
        value = Decimal(str(price).strip())
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"Invalid price {price!r}") from e

    if not value.is_finite():
        raise ValueError(f"Invalid price {price!r}")
    if value < 0:
        raise ValueError(f"Price must not be negative: {price!r}")

    scaled = value.scaleb(decimals)  # exact base-10 shift: 0.0157 -> 15700
    if scaled != scaled.to_integral_value(rounding=ROUND_HALF_UP):
        raise ValueError(
            f"Price {price!r} has more than {decimals} decimal places and cannot be represented in atomic units"
        )
    return int(scaled)


def units_to_price(units: int, decimals: int) -> str:
    """Inverse of price_to_units, for display. 15700 -> '0.0157'."""
    value = Decimal(int(units)).scaleb(-decimals)
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"
