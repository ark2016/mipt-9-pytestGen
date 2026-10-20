from shop.pricing import discount, loyalty_level


def test_discount_new_regular_customer_pays_full_price() -> None:
    assert discount(200.0, "regular", 0) == 200.0


def test_discount_vip_gets_base_rate() -> None:
    assert discount(100.0, "vip", 1) == 85.0


def test_loyalty_level_bronze_for_small_balance() -> None:
    assert loyalty_level(10) == "bronze"
