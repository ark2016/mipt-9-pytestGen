from __future__ import annotations

from pathlib import Path

import pytest

from pytestgen.codegen import GENERATED_HEADER, render_test_module
from pytestgen.domain.models import ModuleInfo, TestSuite
from pytestgen.testfiles import LocalTestFiles, discover_existing_tests, is_generated


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_discovery_skips_hidden_dirs_the_output_and_generated_files(tmp_path: Path) -> None:
    wanted = write(tmp_path, "tests/test_calc.py", "from calc import grade\n")
    suffix = write(tmp_path, "tests/calc_test.py", "import calc\n")
    write(tmp_path, "tests/test_unrelated.py", "import os\n")
    write(tmp_path, "tests/test_calculator.py", "calculator only\n")
    write(tmp_path, "tests/helper_calc.py", "import calc\n")
    write(tmp_path, ".venv/lib/test_calc.py", "import calc\n")
    write(tmp_path, "node_modules/test_calc.py", "import calc\n")
    output = write(tmp_path, "tests/test_calc_pytestgen.py", "import calc\n")
    write(tmp_path, "tests/test_old_gen.py", f'{GENERATED_HEADER} for calc."""\nimport calc\n')
    found = discover_existing_tests(tmp_path, "pkg.calc", output)
    assert found == (suffix.resolve(), wanted.resolve())


def test_the_detected_header_is_the_one_the_renderer_writes(tmp_path: Path) -> None:
    """One constant on both sides: the renderer's header must be recognised as generated."""
    module = ModuleInfo(path=tmp_path / "calc.py", module_name="calc", source="")
    text = render_test_module(TestSuite(module))
    assert text.startswith(GENERATED_HEADER)
    assert is_generated(write(tmp_path, "tests/test_calc_pytestgen.py", text))


@pytest.mark.parametrize("prefix", ["", "\n\n  ", "﻿"])
def test_generated_files_are_recognised_despite_leading_blanks_and_bom(
    tmp_path: Path, prefix: str
) -> None:
    path = write(tmp_path, "t.py", f'{prefix}{GENERATED_HEADER} for calc."""\n')
    assert is_generated(path)


def test_foreign_missing_and_unreadable_files_are_not_generated(tmp_path: Path) -> None:
    assert not is_generated(write(tmp_path, "mine.py", "def test_mine():\n    pass\n"))
    assert not is_generated(tmp_path / "missing.py")
    assert not is_generated(tmp_path)  # a directory cannot be read


def test_local_store_writes_utf8_with_unix_newlines_and_creates_directories(
    tmp_path: Path,
) -> None:
    store = LocalTestFiles()
    target = tmp_path / "deep" / "dir" / "test_x.py"
    assert not store.exists(target)
    assert store.write(target, "a = 'é'\nb = 2\n") == target
    assert store.exists(target)
    assert target.read_bytes() == "a = 'é'\nb = 2\n".encode()


def test_local_store_delegates_discovery_and_generated_detection(tmp_path: Path) -> None:
    store = LocalTestFiles()
    found = write(tmp_path, "tests/test_calc.py", "import calc\n")
    own = write(tmp_path, "tests/test_calc_pytestgen.py", f'{GENERATED_HEADER} for calc."""\n')
    assert store.discover(tmp_path, "calc", own) == (found.resolve(),)
    assert store.is_generated(own) and not store.is_generated(found)
