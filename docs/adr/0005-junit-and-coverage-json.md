# 0005. Результаты прогона через `junit.xml` и coverage JSON

Статус: принято.

## Контекст

Нужны структурированные исходы тестов и покрытие по строкам и дугам (branch coverage) только
для целевого модуля. Можно вызвать `pytest.main()` внутри процесса pytestgen и собрать данные
плагином, но тогда тестируемый код грузится в процесс инструмента: общее состояние
`sys.modules`, утечки, невозможность убить зависший тест, а `coverage` конфликтует с покрытием
самого pytestgen.

## Решение

Каждый прогон - отдельный подпроцесс (`sandbox/runner.py`):

```text
python -m coverage run --branch --include=<target> -m pytest <files>
       --junitxml=junit.xml --timeout=<N> -p no:cacheprovider -o addopts=
python -m coverage json --include=<target>
```

- `sandbox/junit.py` разбирает JUnit XML (`junit_family=legacy`) в доменные `TestOutcome`.
- `sandbox/coverage_json.py` извлекает executed/missing строки и дуги целевого файла.
- Собственный `coveragerc` (`branch = True`) не даёт конфигурации проекта менять формат данных.
- Если pytest-timeout с методом `thread` (единственный на Windows) убил процесс через `os._exit`,
  `junit.xml` не будет. Тогда `sandbox/console.py` читает подробный вывод `-v`,
  виновный тест помечается `TIMEOUT`, исключается через `--deselect`, остальные перезапускаются
  (не более 3 раз, `MAX_TIMEOUT_RERUNS`).

## Последствия

- Изоляция и надёжный таймаут на уровне процесса, форматы стабильны и легко подменяются в тестах.
- Каждый прогон платит за запуск интерпретатора, поэтому число прогонов ограничено бюджетами
  (`PYTESTGEN_GAP_ITERATIONS`, `PYTESTGEN_MAX_MINIMIZE_RUNS`).
- На Windows после таймаута результаты и покрытие относятся только к оставшимся тестам и могут
  быть неполными (runner дописывает об этом заметку в вывод прогона).
