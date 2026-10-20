# Architecture Decision Records

Короткие записи о ключевых решениях: контекст, решение, последствия. Формат упрощённый
(по мотивам Michael Nygard). Статус у всех записей: принято.

| № | Решение | Запись |
| --- | --- | --- |
| 0001 | Ports & adapters (hexagonal architecture) | [0001-ports-and-adapters.md](0001-ports-and-adapters.md) |
| 0002 | Генерация по одной функции | [0002-per-function-generation.md](0002-per-function-generation.md) |
| 0003 | Concolic-lite: AST path conditions + Z3, LLM как запасной решатель | [0003-concolic-lite.md](0003-concolic-lite.md) |
| 0004 | Subprocess sandbox: таймауты, чистое окружение, rlimits | [0004-subprocess-sandbox.md](0004-subprocess-sandbox.md) |
| 0005 | Результаты прогона через `junit.xml` + coverage JSON | [0005-junit-and-coverage-json.md](0005-junit-and-coverage-json.md) |
| 0006 | Собственный HTTP-клиент: httpx + tenacity, моки через respx | [0006-own-http-client.md](0006-own-http-client.md) |
| 0007 | Неисправимые тесты попадают в отчёт, а не в файл | [0007-unfixable-tests-to-report.md](0007-unfixable-tests-to-report.md) |
| 0008 | `--minimize` опционален | [0008-optional-minimize.md](0008-optional-minimize.md) |
| 0009 | Дисковый кэш ответов LLM, temperature 0.2, seed | [0009-disk-llm-cache.md](0009-disk-llm-cache.md) |
| 0010 | structlog JSON в stderr, rich в stdout | [0010-logging-and-cli-output.md](0010-logging-and-cli-output.md) |
| 0011 | uv, Python 3.12 в Docker/CI, поддержка >= 3.11 | [0011-uv-and-python-versions.md](0011-uv-and-python-versions.md) |
| 0012 | Гибрид: шаблон + LLM-слоты (и `--strategy llm` для сравнения) | [0012-hybrid-template-plus-llm-slots.md](0012-hybrid-template-plus-llm-slots.md) |
