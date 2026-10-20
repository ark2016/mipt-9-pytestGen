# Сценарий живой демонстрации (10 минут)

Цель: за десять минут показать, что pytestgen (1) находит входы для веток через Z3 без модели,
(2) поднимает покрытие демо-проекта, (3) использует маленькую локальную LLM там, где символьный
анализ бессилен, и (4) честно отчитывается о том, что не получилось. Все команды выполняются из
корня репозитория в Git Bash или bash.

## Подготовка (за 30-40 минут до выступления)

1. Зависимости и проверка окружения:

   ```bash
   uv sync
   uv run pytestgen version
   (cd examples && uv run pytest -q)        # 3 passed: стартовая точка
   ```

2. Поднять сервер модели (первый раз скачивает ~1.1 GB, до 15 минут):

   ```bash
   docker compose up -d --wait llm          # или: make llm
   export PYTESTGEN_LLM_BASE_URL=http://localhost:8080/v1
   export PYTESTGEN_LLM_MODEL=qwen2.5-coder-1.5b-instruct
   export PYTHONIOENCODING=utf-8            # Windows: чтобы rich рисовал таблицы без артефактов
   ```

3. **Прогреть кэш** и сохранить запасные результаты. Запустите все шаги ниже один раз с теми же
   аргументами и из той же директории (кэш лежит в `.pytestgen/cache` относительно текущей
   директории; ключ зависит от модели и промпта). Без `--no-cache`! Во второй раз строка
   `LLM usage` должна показывать только попадания в кэш (`N requests, N cache hits`).

   ```bash
   mkdir -p out
   uv run pytestgen generate examples/shop/pricing.py --project-root examples \
       -o out/test_pricing_generated.py --report-md out/report.md --report-json out/report.json
   uv run pytestgen --log-level INFO --log-format console generate examples/shop/cart.py \
       --project-root examples -f Cart -o out/test_cart_generated.py --report-md out/report_cart.md
   cp -r out out.backup                      # запасной набор результатов
   ```

4. Открыть заранее: `examples/shop/pricing.py` (функция `free_shipping` внизу), `out.backup/report.md`,
   `.github/workflows/pr-tests.yml`, вкладку с подготовленным pull request и его комментарием
   pytestgen (откройте PR с небольшим изменением в `src/` заранее; скриншот - запасной вариант).
5. Шрифт терминала крупный, `COLUMNS=120` (`export COLUMNS=120`), чтобы таблицы не переносились.

## Таймлайн

| Время | Блок | Главное |
| --- | --- | --- |
| 0:00-1:00 | Постановка | Что делаем и с чем сравниваем |
| 1:00-3:00 | `analyze`: пути и Z3 | Модели не нужно |
| 3:00-6:30 | `generate` на `pricing.py` | Покрытие, отчёт, подозрительный `free_shipping` |
| 6:30-8:30 | `generate` на `cart.py` | Методы экземпляра через LLM, repair |
| 8:30-9:30 | GitHub Action на PR | Комментарий с отчётом |
| 9:30-10:00 | Ограничения и выводы | Честная картина |

### 0:00-1:00. Постановка

Говорим: генератор тестов на малой локальной модели (Qwen2.5-Coder 1.5B на CPU, без GPU);
модель сама не умеет надёжно писать целые тесты, поэтому каркас делает Z3, а модель заполняет
короткие слоты. Показываем `examples/shop/pricing.py` и стартовое покрытие:

```bash
(cd examples && uv run pytest -q)
```

Три существующих теста покрывают 37.5% строк модуля. В `free_shipping` спрятан баг: docstring
обещает порог "100 или более", код проверяет `> 100`. Спросить зал, увидят ли это тесты.

### 1:00-3:00. `analyze`: пути и Z3

```bash
uv run pytestgen analyze examples/shop/pricing.py --project-root examples -f discount -f free_shipping
```

Что показать на экране:

- таблица целей: для каждой ветки обе стороны (`discount:18:T` и `discount:18:F`), условие и
  накопленный path condition;
- колонка Z3: например, для `discount:18:T` найдено `customer_type='vip', years=3`, для
  `discount:20:T` - `customer_type='regular', years=2`; значения короткие и читаемые
  (мягкие предпочтения оптимизатора), каждый вход перепроверен в семантике Python;
- у `free_shipping` веток нет ("no branches"): решатель тут бесполезен, поэтому pytestgen добавляет
  граничные значения вокруг констант сравнения (`99`, `100`, `101`).

Показать границу возможностей:

```bash
uv run pytestgen analyze examples/shop/cart.py --project-root examples -f Cart.remove_item
```

Для методов экземпляра в колонке Z3 написано "instance methods are handled by the LLM".

### 3:00-6:30. `generate` на `pricing.py`

```bash
uv run pytestgen generate examples/shop/pricing.py --project-root examples \
    -o out/test_pricing_generated.py --report-md out/report.md --report-json out/report.json
```

Пока идёт прогон (с прогретым кэшем это секунды), комментируем стадии: `analyze`, `baseline`,
`solve`, `record` (функция выполняется в sandbox, ожидаемые значения - это записанное поведение),
`llm_slots` (ревью оракула, входы для нерешённых веток), `run`, `gap`.

Что показать в итоговой сводке:

- Coverage Before/After: строки 37.5% и ожидаемо не ниже 95.8% (столько даёт один Z3 без
  модели; запас - работа LLM и gap loop), ветви с 26.5%;
- таблицу "Kept tests by origin": сколько тестов дал `z3` и сколько `llm_slot` / `llm_free`,
  и сколько дуг каждый источник добавил;
- таблицу по функциям: `shipping_cost` и `discount` доходят до 100%, `validate_promo` - самая
  трудная (строковые условия).

Подозрительное место. Откройте `out/report.md`, раздел **Suspected bugs**. Ожидаемо там
`free_shipping` с входом `order_total=100.0`: наблюдалось `False`, а docstring говорит, что от 100
включительно доставка бесплатная. Такой кейс **не попадает** в сгенерированный файл, чтобы не
закрепить баг в регрессионных тестах.

> Если модель баг не заметила (малая модель может промахнуться), скажите прямо: оракул опирается
> на docstring и на модель. Покажите в `out/test_pricing_generated.py` строку
> `pytest.param(100.0, False, ...)` в `test_free_shipping_returns` - вот ловушка тестов, которые
> фиксируют поведение, а не спецификацию. Это одно из ограничений из README.

Запустить результат отдельно:

```bash
PYTHONPATH=examples uv run pytest out/test_pricing_generated.py -q
```

Сгенерированный файл зелёный: всё, что не прошло, осталось в отчёте (раздел "Dropped tests").

### 6:30-8:30. `generate` на `cart.py`: LLM free-form и repair

```bash
uv run pytestgen --log-level INFO --log-format console generate examples/shop/cart.py \
    --project-root examples -f Cart -o out/test_cart_generated.py --report-md out/report_cart.md
```

`Cart.add_item`, `remove_item`, `total` - методы экземпляра, Z3 их не берёт, поэтому модель пишет
целые функции pytest. `Cart.parse_qty` - `staticmethod`, он идёт по гибридному пути. Что показать:

- в консольных логах (формат `console`, вперемешку с прогрессом) события `tests_written` (сколько тестов принято, сколько отклонил guard);
- если будет упавший тест: стадия `repair`, события `test_repaired` или `test_given_up`; починка
  идёт по traceback, не более двух попыток;
- в отчёте раздел "Dropped tests" с причинами (`still_failing`, `invalid_code`);
- итоговое покрытие `cart.py` и вклад источника `llm_free` в таблице по источникам.

Результат зависит от модели и может отличаться от запуска к запуску; поэтому показывается
прогретый прогон из кэша. Фраза для зала: "именно для методов экземпляра качество модели
определяет всё".

Необязательный бонус, если остаётся время (запускать только с прогретым кэшем и без `--no-cache`):
сравнение со стратегией "только LLM" - тот же модуль, `--strategy llm`, затем сравнить прирост
покрытия и число отброшенных тестов.

### 8:30-9:30. GitHub Action на pull request

Показать [`.github/workflows/pr-tests.yml`](../.github/workflows/pr-tests.yml) (шаг
`uses: ./`) и открыть подготовленный pull request:

- один комментарий с маркером `<!-- pytestgen-report -->`: сводная таблица по файлам и отчёт
  каждого файла; новый push обновляет тот же комментарий;
- в Job Summary тот же отчёт; сгенерированные тесты лежат артефактом `pytestgen-tests`;
- в ветку PR ничего не коммитится; PR из форка получает отчёт без комментария.

LLM для action: либо секрет и переменные репозитория (облачный endpoint), либо llama.cpp с
Qwen2.5-Coder-0.5B, который workflow поднимает сам на раннере.

### 9:30-10:00. Ограничения и выводы

Три тезиса:

1. Каркас от Z3 даёт большую часть покрытия без модели; модель добавляет там, где нужна семантика
   (docstring, объекты).
2. Результат проверяемый: файл всегда зелёный, отброшенное и подозрительное видно в отчёте.
3. Ограничения (раздел README): малая модель, concolic-lite понимает только простые условия,
   sandbox не является границей безопасности, тесты фиксируют поведение.

## План Б

| Проблема | Что делать |
| --- | --- |
| Модель отвечает медленно | Кэш прогрет: все запросы берутся с диска, остаётся только Z3 и sandbox (на `pricing.py` без модели это около 7 секунд на нашей машине). Если кэш по какой-то причине не сработал, откройте `out.backup/`. |
| Сервер модели не поднялся или недоступен | Запустите те же команды: circuit breaker отключит LLM после первой ошибки соединения, и покажется результат на одном Z3 (на `pricing.py`: 37.5% до 95.8% строк). Для `cart.py` без модели тестов не будет вовсе (методы экземпляра идут только через LLM), таблица покрытия там не содержательна (`n/a`): вместо неё покажите `out.backup/report_cart.md`. Результаты LLM-части покажите из `out.backup/`. |
| Нет сети и модель не скачана | Заранее скачайте GGUF (первый запуск `docker compose up -d --wait llm` в хорошей сети), volume `llm-models` хранит его локально. Иначе сценарий выше с Z3-only и `out.backup/`. |
| Кэш пуст (другая директория, другая модель) | Ключ кэша зависит от модели и текста запроса, а каталог - от `PYTESTGEN_CACHE_DIR` (по умолчанию относительный путь). Запускайте из корня репозитория и с той же моделью, что при прогреве. |
| Демо через Docker | `docker compose run --rm --build pytestgen`; результаты в `./out`, кэш в `./out/.cache`; после прогрева повторный запуск идёт из кэша. |
| Нужен быстрый ответ, а модель тормозит | Переключитесь на более лёгкую модель: `LLM_HF_REPO=Qwen/Qwen2.5-Coder-0.5B-Instruct-GGUF`, `LLM_MODEL_NAME=qwen2.5-coder-0.5b-instruct` в `.env` (ключ кэша изменится, прогрейте заново). |
| Порт 8080 занят | `LLM_HOST_PORT=8081` в `.env` и `PYTESTGEN_LLM_BASE_URL=http://localhost:8081/v1`. |

## Типичные вопросы

- **Почему не отдать модели всё?** Для сравнения есть `--strategy llm`: модель сама пишет тесты на
  все функции. Ожидаемо, что на малой модели такой путь даёт больше отброшенных тестов, но на
  реальной модели это не замерялось (см. [ADR 0012](adr/0012-hybrid-template-plus-llm-slots.md)).
- **Откуда ожидаемые значения в тестах?** Из выполнения функции в sandbox; это характеризационные
  тесты. Оракул-ревью по docstring - противовес ([ADR 0007](adr/0007-unfixable-tests-to-report.md)).
- **Это безопасно?** Нет, это не граница безопасности ([ADR 0004](adr/0004-subprocess-sandbox.md)); для
  чужого кода нужен контейнер.
- **Откуда идея concolic?** Из курсового проекта автора о совмещении фаззинга и динамического
  символьного выполнения ([ADR 0003](adr/0003-concolic-lite.md)).
- **Как подключить свою модель?** Поменять `PYTESTGEN_LLM_BASE_URL` и `PYTESTGEN_LLM_MODEL`;
  подойдёт любой OpenAI-совместимый сервер.
