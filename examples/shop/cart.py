"""Shopping cart that applies the loyalty discount from ``pricing``."""

from .pricing import discount


class Cart:
    """Holds line items keyed by product name and totals them with discounts."""

    MAX_QTY = 100

    def __init__(self, customer_type: str = "regular", years: int = 0) -> None:
        """Create an empty cart for a customer of the given type and tenure."""
        self._items: dict[str, tuple[float, int]] = {}
        self._customer_type = customer_type
        self._years = years

    def add_item(self, name: str, price: float, qty: int = 1) -> None:
        """Add ``qty`` units of a product, merging with an existing line.

        Raises ValueError for an empty name, a non-positive price,
        or a quantity outside 1..MAX_QTY (counting the merged total).
        """
        if not name.strip():
            raise ValueError("name must not be empty")
        if price <= 0:
            raise ValueError("price must be positive")
        if not 1 <= qty <= self.MAX_QTY:
            raise ValueError(f"qty must be between 1 and {self.MAX_QTY}")
        old_qty = self._items.get(name, (price, 0))[1]
        if old_qty + qty > self.MAX_QTY:
            raise ValueError(f"total qty for {name!r} exceeds {self.MAX_QTY}")
        self._items[name] = (price, old_qty + qty)

    def remove_item(self, name: str) -> None:
        """Remove a product line entirely. Raises KeyError if it is not in the cart."""
        if name not in self._items:
            raise KeyError(name)
        del self._items[name]

    def total(self) -> float:
        """Return the cart total after the customer's loyalty discount."""
        subtotal = sum(price * qty for price, qty in self._items.values())
        return discount(subtotal, self._customer_type, self._years)

    @staticmethod
    def parse_qty(text: str) -> int:
        """Parse a quantity typed by a user, such as ' 3 ', into an int in 1..MAX_QTY."""
        cleaned = text.strip()
        if not cleaned.isdecimal():
            raise ValueError(f"quantity must be a whole number, got {text!r}")
        value = int(cleaned)
        if not 1 <= value <= Cart.MAX_QTY:
            raise ValueError(f"quantity out of range: {value}")
        return value
