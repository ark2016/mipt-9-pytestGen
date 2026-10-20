"""Pricing rules for the demo shop. All amounts are in abstract currency units."""

RESERVED_CODES = frozenset({"SAVE000", "DEMO-VIP"})


def discount(price: float, customer_type: str, years: int) -> float:
    """Return the price after the loyalty discount.

    Rules:
    - ``vip`` customers get 15% off, plus 5% more after 3 years.
    - ``regular`` customers get 5% off after 2 years.
    - Any other customer type gets no discount.
    """
    if price < 0 or years < 0:
        raise ValueError("price and years must be non-negative")
    if customer_type == "vip":
        rate = 0.15
        if years >= 3:
            rate += 0.05
    elif customer_type == "regular" and years >= 2:
        rate = 0.05
    else:
        rate = 0.0
    return round(price * (1 - rate), 2)


def shipping_cost(weight_kg: float, express: bool, country: str | None) -> float:
    """Return the shipping cost for a parcel.

    Base cost grows with weight in three bands, express doubles it,
    and foreign destinations add a surcharge. ``None`` means domestic.
    """
    if weight_kg <= 0:
        raise ValueError("weight must be positive")
    if weight_kg <= 1.0:
        cost = 5.0
    elif weight_kg <= 10.0:
        cost = 5.0 + 1.5 * (weight_kg - 1)
    else:
        cost = 5.0 + 1.5 * 9 + 2.0 * (weight_kg - 10)
    if express:
        cost *= 2
    if country is None or country == "RU":
        surcharge = 0.0
    elif country in {"KZ", "BY"}:
        surcharge = 3.0
    else:
        surcharge = 10.0
    return round(cost + surcharge, 2)


def validate_promo(code: str) -> bool:
    """Return True if a promo code has an accepted form.

    Accepted codes are 6 to 12 characters long, not reserved, and either
    ``SAVE`` followed by digits or ending with ``-VIP``.
    """
    if not 6 <= len(code) <= 12:
        return False
    if code in RESERVED_CODES:
        return False
    if code.startswith("SAVE"):
        return code[4:].isdigit()
    return code.endswith("-VIP")


def loyalty_level(points: int) -> str:
    """Return the loyalty tier name for a number of points."""
    if points < 0:
        raise ValueError("points must be non-negative")
    if points >= 10_000:
        return "platinum"
    elif points >= 5_000:
        return "gold"
    elif points >= 1_000:
        return "silver"
    else:
        return "bronze"


def free_shipping(order_total: float) -> bool:
    """Return True when an order of 100 or more qualifies for free shipping."""
    # NOTE: intentional bug for the demo (docstring says >= 100, code uses > 100)
    return order_total > 100
