# Презентация защиты pytestgen

8 слайдов на русском, 16:9. Основной результат после подстановки завершённых экспериментов:
`pytestgen-defense.pptx`, `pytestgen-defense.pdf`, `previews/slide-01.png` … `slide-08.png`.
Черновик всегда называется `qa/draft/pytestgen-defense-draft.pptx` и помечен на каждом слайде.

## Контракт источников

`evidence.example.json` задаёт схему версии 1. Скопируйте её в `evidence.json` и заполните:
- `source_commit`: коммит приложения для новых экспериментов.
- `mbpp.report_path`: исходный JSON `scripts/bench_mbpp.py`, `summary` и `tasks`.
- `heuristic.report_path`: объединённый JSON `scripts/bench_heuristic.py`, 32 записи `repairs` и 32 `oracles`.
- `library.report_path`: JSON `scripts/bench_library.py`, полный `baseline` и режимы `existing`, `cold`.
- `heuristic.completed`, `library.completed`: true только после завершения прогонов и сохранения полного отчёта.
- `*.evidence_url`: URL запуска CI или опубликованного исходного JSON.
- `heuristic.interpretation_notes`: проверенные содержательные замечания к объяснениям и примерам, включаемые в заметки докладчика.
- `ci`: завершённый CI, URL, коммит, версия Python, passed/skipped/deselected, coverage_percent, checks.

Пути отчётов разрешаются относительно корня репозитория. Файл evidence может находиться в другом каталоге и передаётся через `--evidence`.
Числа считываются из сырых отчётов. Матрицы перепроверяются по записям, а не принимаются из summary вслепую.
Непроверенные/вырожденные repair учитываются отдельно. Расширенная repair_all_passing_rewrites включает вырожденные переписывания симметрично для обоих типов сценария. Коммит исходного отчёта должен совпадать с source_commit и CI. Текущие материалы используют c99474b, повторная сборка принимает новый согласованный набор evidence. На слайде oracle_reviewed содержит только явные вердикты. Oracle_all в заметках трактует UNREVIEWED как отсутствие предупреждения, число пропусков раскрывается отдельно.
Доли MBPP относятся к сохранённым тестам. Знаменатель mutation score включает непроверенные запуски мутантов.
Генератор не запускает модели, не меняет исходные отчёты и не переписывает приложение.

## Сборка

Зависимость изолирована в игнорируемом каталоге:
```powershell
npm.cmd install --prefix out/presentation-tools --save-exact --ignore-scripts --no-audit --no-fund pptxgenjs@4.0.1
node docs/presentation/generate.cjs --evidence docs/presentation/evidence.json
powershell -NoProfile -ExecutionPolicy Bypass -File docs/presentation/render.ps1
```

Генератор финального файла отказывается работать с неполными данными.
Для внутреннего макета:
```powershell
node docs/presentation/generate.cjs --evidence docs/presentation/evidence.example.json --draft
powershell -NoProfile -ExecutionPolicy Bypass -File docs/presentation/render.ps1 -Draft
```

PowerPoint COM открывает файл без видимого окна, экспортирует PDF и PNG и записывает проверку границ текста.
Таблицы, диаграмма MBPP и архитектурные схемы остаются нативными редактируемыми объектами PowerPoint.
`qa/` хранит результаты проверки, черновики и промежуточные материалы.
Финальные файлы необходимо просмотреть по всем восьми слайдам после каждого содержательного обновления.

## Выступление

`speaker-notes.md` создаётся из тех же заметок, что встроены в PPTX.
Самостоятельный обзор всех 8 слайдов рассчитан примерно на 5 минут. В 10-минутной живой защите используются только выбранные слайды вместе с CLI по `docs/demo.md`, без дополнительного 5-минутного доклада.
Дата материалов и измерений: 13 ноября 2026 года. Дата защиты назначается отдельно. Автор: Аркадий Лебедев, МФТИ.
