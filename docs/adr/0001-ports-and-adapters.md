# 0001. Ports & adapters (hexagonal architecture)

Статус: принято.

## Контекст

В пайплайне четыре внешних зависимости, которые трудно тестировать и легко менять: LLM по HTTP,
подпроцессы с pytest и coverage, файловая система и Z3. Целевая модель маленькая (Qwen2.5-Coder
1.5B/3B), её ответы недетерминированы, а реальные прогоны медленные. Тесты самого pytestgen в CI
обязаны идти без реальной модели.

## Решение

- `domain/` содержит только dataclass-ы и enum-ы без I/O (`models.py`, `llm.py`, `annotations.py`).
- `ports.py` описывает интерфейсы как `typing.Protocol`: `ModuleAnalyzer`, `ModuleIntrospector`,
  `HintSolver`, `LLMClient`, `LLMCache`, `SuiteRunner`, `ExecutionRecorder`, `ReportRenderer`,
  `ProgressListener`.
- `application/` реализует сценарии (`AnalyzeModule`, `GenerateTests`) и зависит только от
  `domain` и `ports`.
- Адаптеры (`analysis`, `concolic`, `llm`, `sandbox`, `report`, `testfiles`, `codegen`)
  реализуют порты.
- `container.py` (composition root) - единственное место, где выбирается конкретный адаптер.
  `cli.py` вызывает только `build_analyze` и `build_generate`.

## Последствия

- Все ветки пайплайна проверяются на fakes без сети и без подпроцессов
  (`tests/unit/application/`), поэтому покрытие высокое (около 99%), а CI не зависит от модели.
- Замена бэкенда (другой решатель, другой раннер) не затрагивает `application/`.
- Цена: больше файлов и косвенности, чем в скрипте; порты надо держать узкими. Правило
  "application не тянет адаптеры и тяжёлые библиотеки (httpx, tenacity, z3)" проверяет тест
  `tests/unit/application/test_app_layering.py`.
