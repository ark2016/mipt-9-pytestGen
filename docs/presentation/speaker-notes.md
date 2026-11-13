# Заметки к защите pytestgen

## 1. pytestgen

1. pytestgen
Время: около 15 секунд.

Я представляю pytestgen: генератор pytest-тестов, в котором символьный анализ строит основу, а небольшая локальная языковая модель помогает на сложных участках. Коротко покажу устройство и измерения, затем перейду к CLI. Вся живая демонстрация укладывается в десять минут, для её поддержки использую выбранные слайды.

Определения и ограничения:
Автор: Аркадий Лебедев, МФТИ. Полный самостоятельный обзор слайдов занимает около пяти минут. На десятиминутной защите используются только выбранные слайды вместе с живым CLI, пять минут к десяти не добавляются.

Источники:
https://github.com/ark2016/mipt-9-pytestGen
https://github.com/ark2016/mipt-9-pytestGen/blob/c99474b8e3e752768e48ba9d8c98b559faaef604/docs/demo.md

## 2. Гибридный пайплайн

2. Гибридный пайплайн
Время: около 40 секунд.

AST выделяет функции, ветви и условия путей. Z3 ищет входы, а вокруг простых сравнений добавляются граничные значения. Программа выполняется, и наблюдаемый результат попадает в шаблон pytest. Модель предлагает дополнительные входы, проверяет оракул по описанию и пишет свободные тесты. Затем всё запускается, падающие свободные тесты можно исправить, а по покрытию запускается дополнительный цикл. При недоступной модели остаётся символьная часть.

Определения и ограничения:
Записанное поведение не гарантирует правильность. Функция выполняется дважды для проверки стабильности. Методы экземпляров и --strategy llm используют free-form путь. Неудачные табличные кейсы отбрасываются. Непригодный ответ модели не прерывает весь конвейер.

Источники:
https://github.com/ark2016/mipt-9-pytestGen/blob/c99474b8e3e752768e48ba9d8c98b559faaef604/docs/architecture.md
https://github.com/ark2016/mipt-9-pytestGen/blob/c99474b8e3e752768e48ba9d8c98b559faaef604/src/pytestgen/application/generate.py
https://github.com/ark2016/mipt-9-pytestGen/blob/c99474b8e3e752768e48ba9d8c98b559faaef604/src/pytestgen/container.py

## 3. Архитектура и инженерные проверки

3. Архитектура и инженерные проверки
Время: около 40 секунд.

Сценарии приложения работают через порты, а AST, Z3, клиент модели, раннер и рендерер отчёта подключены адаптерами. Это позволяет менять реализации независимо. Генерируемый код выполняется во временной копии в subprocess с таймаутом. Docker запускает всё приложение, но не создаёт отдельный контейнер на каждый тест. В CI проверяются стиль, типы, тесты и сборка образа. CI: 1864 passed, 4 skipped, покрытие 98,60% (Python 3.12).

Определения и ограничения:
Под subprocess подразумевается изоляция исполнения, а не надёжная песочница для враждебного кода. POSIX дополнительно ограничивает CPU и адресное пространство, Windows этих ограничений не имеет. Доступ к сети и разрешённым файлам сохраняется.
CI: {"completed":true,"run_url":"https://github.com/ark2016/mipt-9-pytestGen/actions/runs/43495706627","commit":"c99474b8e3e752768e48ba9d8c98b559faaef604","python":"3.12","passed":1864,"skipped":4,"deselected":2,"coverage_percent":98.6,"checks":["Ruff","Ruff format","mypy strict","pre-commit","uv lock","pytest 3.11/3.12","Docker build + CLI smoke"]}. Покрытие относится к проекту pytestgen, а не к MBPP или python-slugify. Обычный CI использует mock LLM.

Источники:
https://github.com/ark2016/mipt-9-pytestGen/blob/c99474b8e3e752768e48ba9d8c98b559faaef604/docs/architecture.md
https://github.com/ark2016/mipt-9-pytestGen/blob/c99474b8e3e752768e48ba9d8c98b559faaef604/docs/adr/0004-subprocess-sandbox.md
https://github.com/ark2016/mipt-9-pytestGen/blob/c99474b8e3e752768e48ba9d8c98b559faaef604/docker-compose.yml
https://github.com/ark2016/mipt-9-pytestGen/actions/runs/43495706627

## 4. MBPP

4. MBPP
Время: около 45 секунд.

На фиксированной выборке из ста задач 56 дали хотя бы один тест, прошедший эталон. В 43 задачах таких тестов нет, один эталон невалиден. Из 353 сохранённых тестов 352 проходят эталон, а 224 ещё и убивают хотя бы один мутант. Убиты 104 из 174 допустимых мутантов. Эти проценты нельзя переносить на все задачи: они условны по сохранённым тестам. Четыре запуска мутантов остались непроверенными.

Определения и ограничения:
Источник: docs/evaluation/results/mbpp-report.json.
seed=42, dataset revision=f82046ba5aabbbb427dbfd38a254d26bff08b533, strategy=hybrid, model=qwen2.5-coder-1.5b-instruct, max_mutants=3.
Когорта: 56+43+1=100. Доли: 352/353, 224/353, 104/174. Знаменатель mutation score включает killed+survived+unverified. Подбор мутантов выполняется по эталонным тестам MBPP, их исходники не передаются генератору. Эти результаты относятся к историческому запуску 42826762843. Единственное падение сохранённого теста на эталоне не объясняется предположением о причине. Это оценка одного режима, не сравнение с другой моделью или инструментом.

Источники:
https://github.com/ark2016/mipt-9-pytestGen/actions/runs/42826762843
https://github.com/ark2016/mipt-9-pytestGen/blob/c99474b8e3e752768e48ba9d8c98b559faaef604/scripts/bench_mbpp.py

## 5. Методика оценки эвристики

5. Методика оценки эвристики
Время: около 40 секунд.

Чтобы оценить предупреждение о подозрительном repair, взяты восемь небольших функций с заранее заданным контрактом и мутантом. Для каждой есть вариант с docstring и без него. В одном сценарии исправный тест обнаруживает ошибку кода, в другом ошибочный тест падает на корректном коде. Получается 32 сценария. После ремонта тест проверяется на мутанте и эталоне. Отдельные 32 наблюдения оценивают oracle review. Набор включает неверные границы, арифметику и исключения. Правило предупреждения не подгоняется по результатам.

Определения и ограничения:
Production-правило требует docstring и assertion failure. До repair свидетели проверяются: корректный тест проходит эталон, ошибочный тест падает на эталоне, корректный тест падает на мутанте. Repair-классификация требует прошедшего теста с assert/pytest.raises, который исполнил целевой return. Вырожденные тесты и другие неуспехи не включаются в основную матрицу и раскрываются отдельно. Непроверенные исправления на эталоне исключаются. Дополнительная метрика repair_all_passing_rewrites включает вырожденные переписывания симметрично в обоих типах сценария. На mutant успешный repair обозначает маскировку: изменение оракула или уход от свидетельствующего входа. Это малая специально подобранная выборка, не оценка всей экосистемы Python. Исключения: safe_divide при нуле, reciprocal с отсутствующим raise.

Источники:
https://github.com/ark2016/mipt-9-pytestGen/blob/c99474b8e3e752768e48ba9d8c98b559faaef604/scripts/bench_heuristic.py
https://github.com/ark2016/mipt-9-pytestGen/blob/c99474b8e3e752768e48ba9d8c98b559faaef604/scripts/repair_cases.py
https://github.com/ark2016/mipt-9-pytestGen/blob/c99474b8e3e752768e48ba9d8c98b559faaef604/src/pytestgen/application/suspicion.py

## 6. Результат оценки эвристики

6. Результат оценки эвристики
Время: около 45 секунд.

Основная матрица repair включает 11 из 32 сценариев. Сигнал означает предупреждение о подозрительном исправлении. Precision равна 0,0%, recall 0,0%. Рядом показаны только рассмотренные случаи oracle review: precision 50,0%, recall 12,5%. Число сценариев вне основной матрицы repair: 21. Поэтому эти доли описывают только классифицированные исходы, а не все попытки ремонта. На этой выборке ложных сигналов repair: 5, пропущенных маскировок: 1. Recall для docstring-группы: н/д. В oracle review 16 случаев остались UNREVIEWED. Единственный TP oracle, safe_divide/doc/mutant, означает совпадение бинарного предупреждения с меткой. Объяснение модели инвертировано: она пишет, что код возвращает ноль вопреки обещанному исключению. В действительности контракт требует 0, а мутант поднимает ZeroDivisionError. Это не корректная диагностика бага. Единственная маскировка repair, sum_values/no-doc/bug: правильный результат для пустого списка 0 заменён в тесте на 1. Предупреждение не сработало.

Определения и ограничения:
Источник: docs/evaluation/results/heuristic-report.json.
TP: истина положительна и сигнал есть. FN: истина положительна, сигнала нет. FP: сигнал на отрицательном случае. TN: отрицательный случай без сигнала. Repair-положительный исход: actual_masking. Oracle-положительный исход: actual_bug. Precision=TP/(TP+FP), recall=TP/(TP+FN), н/д при нулевом знаменателе. UNREVIEWED oracle считается operational no alert; число воздержаний показано отдельно.
На слайде показан oracle_reviewed, только ok/suspicious. Дополнительно oracle_all трактует UNREVIEWED как отсутствие сигнала. Все расширенные counts и stratified documented/undocumented перепроверяются по исходным записям. Уточнения по данным: {
  "repair_status": {
    "classified": 11,
    "not_repaired": 21
  },
  "repair_documented": {
    "n": 5,
    "tp": 0,
    "fp": 5,
    "tn": 0,
    "fn": 0,
    "precision": 0,
    "recall": null,
    "false_positive_rate": 1
  },
  "repair_undocumented": {
    "n": 6,
    "tp": 0,
    "fp": 0,
    "tn": 5,
    "fn": 1,
    "precision": null,
    "recall": 0,
    "false_positive_rate": 0
  },
  "repair_all_passing_rewrites": {
    "n": 11,
    "tp": 0,
    "fp": 5,
    "tn": 5,
    "fn": 1,
    "precision": 0,
    "recall": 0,
    "false_positive_rate": 0.5
  },
  "oracle_reviewed": {
    "n": 16,
    "tp": 1,
    "fp": 1,
    "tn": 7,
    "fn": 7,
    "precision": 0.5,
    "recall": 0.125,
    "false_positive_rate": 0.125
  },
  "oracle_all": {
    "n": 32,
    "tp": 1,
    "fp": 1,
    "tn": 15,
    "fn": 15,
    "precision": 0.5,
    "recall": 0.0625,
    "false_positive_rate": 0.0625
  },
  "oracle_abstentions": 16
}.
Аналитические примечания: Единственный TP oracle, safe_divide/doc/mutant, означает совпадение бинарного предупреждения с меткой. Объяснение модели инвертировано: она пишет, что код возвращает ноль вопреки обещанному исключению. В действительности контракт требует 0, а мутант поднимает ZeroDivisionError. Это не корректная диагностика бага. Единственная маскировка repair, sum_values/no-doc/bug: правильный результат для пустого списка 0 заменён в тесте на 1. Предупреждение не сработало.
Модель: qwen2.5-coder-1.5b-instruct. case_sha256: 79c771dd91ec7924a05d30fdd6ee31211c18a7044fb7391127a4dab59ddbad0b. До 2 попыток repair.

Источники:
https://github.com/ark2016/mipt-9-pytestGen/actions/runs/43495705978
https://github.com/ark2016/mipt-9-pytestGen/blob/c99474b8e3e752768e48ba9d8c98b559faaef604/scripts/bench_heuristic.py

## 7. Реальная библиотека

7. Реальная библиотека
Время: около 45 секунд.

Для проверки на реальном коде выбрана python-slugify версии 8.0.4. Полный upstream-набор даёт исходную точку. В existing генератор видит измеренное покрытие существующих тестов, а cold начинает без них. Затем сгенерированные тесты независимо запускаются отдельно и вместе с upstream. Полный upstream: 82/82 пройдено. Existing: ok, cold: ok. Оба режима: +1 строка (+1,09 п.п.), +1 ветвь (+2,00 п.п.).

Определения и ограничения:
Источник: docs/evaluation/results/library-report.json.
Покрытие ограничено slugify/slugify.py. Пройдено означает pytest outcomes, а не число assert. Existing комбинирует полный upstream и генерацию с baseline. Cold генерируется без existing tests, затем проверяется отдельно и вместе с upstream. Процентный пункт означает разность процентов. * означает непроверенный результат прогона, а не успешный тест. Н/д означает отсутствие валидного измерения, не ноль.
{
  "revision": "f85f9488520148d5f6899b5639199882b605e30a",
  "archive_sha256": "976b48a3bea5356c64e973d1996ccaf9ae8a48bf109fb359384484f5f19b4fc9",
  "application_sha256": "69a02517d937d51d450573fb05edbbc8d632bc214e4e0c2eeff9b93fcf7ab021",
  "benchmark_sha256": "c60fbfd62e51d642b593ec446cb60378bcc21323b975c4a88e382caf915e6568",
  "settings": {
    "llm_model": "qwen2.5-coder-1.5b-instruct",
    "llm_timeout_s": 60,
    "llm_max_retries": 0,
    "llm_temperature": 0.2,
    "llm_max_tokens": 1024,
    "llm_seed": 42,
    "llm_json_mode": "json_schema",
    "cache_enabled": true,
    "cache_dir": ".pytestgen/cache",
    "run_timeout_s": 60,
    "test_timeout_s": 3,
    "record_timeout_s": 10,
    "repair_attempts": 1,
    "gap_iterations": 1,
    "max_cases_per_function": 8,
    "max_minimize_runs": 12,
    "z3_timeout_ms": 2000,
    "log_level": "WARNING",
    "log_format": "json"
  },
  "python": "3.12.16",
  "platform": "Linux-7.0.0-1019-azure-x86_64-with-glibc2.43",
  "dependencies": {
    "pytestgen": "0.1.0",
    "pytest": "9.1.1",
    "coverage": "7.16.2",
    "pytest-timeout": "2.4.0",
    "text-unidecode": "1.3",
    "Unidecode": null,
    "z3-solver": "5.3.1.0",
    "httpx": "0.28.1",
    "pydantic": "2.14.1"
  },
  "baseline_note": "Upstream coverage is already high; little coverage gain is possible.",
  "status": "ok",
  "mode_details": [
    {
      "mode": "existing",
      "status": "ok",
      "detail": "",
      "gain": {
        "line_percentage_points": 1.0869565217391353,
        "branch_percentage_points": 1.9999999999999907,
        "new_lines": [
          56
        ],
        "new_arcs": [
          "55->56"
        ],
        "lost_lines": [],
        "lost_arcs": []
      },
      "usage": {
        "requests": 5,
        "cache_hits": 0,
        "prompt_tokens": 6415,
        "completion_tokens": 1291
      },
      "wall_s": 157.21277698800003,
      "kept_tests": 13,
      "dropped": 0
    },
    {
      "mode": "cold",
      "status": "ok",
      "detail": "",
      "gain": {
        "line_percentage_points": 1.0869565217391353,
        "branch_percentage_points": 1.9999999999999907,
        "new_lines": [
          56
        ],
        "new_arcs": [
          "55->56"
        ],
        "lost_lines": [],
        "lost_arcs": []
      },
      "usage": {
        "requests": 8,
        "cache_hits": 0,
        "prompt_tokens": 8839,
        "completion_tokens": 2164
      },
      "wall_s": 130.39830750699997,
      "kept_tests": 18,
      "dropped": 1
    }
  ]
}
Кэш LLM отключён. Фиксированный seed не гарантирует детерминизм модели. Образцы тестов upstream используются только для исполнения, не для промптов. Одна библиотека не даёт общей оценки применимости. Предупреждение о separator="" не подтверждено как дефект библиотеки. Измерен прирост покрытия: строка 56 и ветвь 55->56 в smart_truncate. Обнаружение реального бага не заявляется. Existing и cold оба прибавили одну строку и одну ветвь к уже высокому upstream-покрытию. Доля покрытия сгенерированными тестами отдельно ниже baseline. Количество тестов и покрытие не доказывают правильность оракула.

Источники:
https://github.com/ark2016/mipt-9-pytestGen/actions/runs/43495705978
https://github.com/un33k/python-slugify/tree/v8.0.4
https://github.com/ark2016/mipt-9-pytestGen/blob/c99474b8e3e752768e48ba9d8c98b559faaef604/scripts/bench_library.py

## 8. Выводы и переход к демонстрации

8. Выводы и переход к демонстрации
Время: около 30 секунд.

Получился работающий инструмент: символьная часть даёт основу, модель расширяет набор, а отчёт позволяет проверить, что сохранено и что отброшено. Главная граница состоит в оракуле: наблюдаемое поведение может содержать баг, и ремонт теста способен его скрыть. Измерения показывают результат на конкретных наборах, а не гарантию корректности. Теперь покажу CLI, сгенерированные тесты, изменение покрытия и подозрительное место на живом примере.

Определения и ограничения:
Команду со слайда выполнить одной строкой из корня репозитория: uv run pytestgen generate examples/shop/pricing.py --project-root examples -o out/test_pricing_generated.py
Расширенная команда с отчётами: uv run pytestgen generate examples/shop/pricing.py --project-root examples -o out/test_pricing_generated.py --report-md out/report.md --report-json out/report.json
Полный десятиминутный сценарий: docs/demo.md. Перед выступлением сервер модели запускается и кэш прогревается по этому сценарию. Во время демонстрации явно назвать cache hits. Самостоятельный обзор всех восьми слайдов занимает примерно пять минут. В десятиминутной живой защите используются только выбранные слайды по docs/demo.md, а не дополнительный пятиминутный доклад.

Источники:
https://github.com/ark2016/mipt-9-pytestGen/blob/c99474b8e3e752768e48ba9d8c98b559faaef604/docs/demo.md
https://github.com/ark2016/mipt-9-pytestGen/blob/c99474b8e3e752768e48ba9d8c98b559faaef604/examples/shop/pricing.py
