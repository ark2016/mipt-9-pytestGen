# examples: демо-проект для pytestgen

Небольшой магазин — цель для генерации тестов во время живого демо (это не часть `src/`).

- `shop/pricing.py` — чистые функции с ветвлениями по int/float/str/bool/Optional: `discount`, `shipping_cost`, `validate_promo`, `loyalty_level`. Хороший вход для гибридного пути (Z3 + запуск в песочнице).
- `shop/pricing.py::free_shipping` — намеренно сломанная функция: docstring обещает порог `>= 100`, код использует `> 100`. Нужна для oracle-review через LLM.
- `shop/cart.py` — класс `Cart`: методы экземпляра (`add_item`, `remove_item`, `total`) идут по свободному пути LLM, статический `Cart.parse_qty` — по гибридному.
- `textkit.py` — строковые утилиты: `slugify`, `is_palindrome` (keyword-only опции), `truncate` (суффикс по умолчанию), `classify_password` (правила длины и классов символов).
- `tests/test_pricing_partial.py` — три существующих теста, покрывают только часть `pricing.py`: базовая точка для замера прироста покрытия.
- `pyproject.toml` — корень демо-проекта: `pythonpath = ["."]`, `testpaths = ["tests"]`, запуск из `examples/`.
