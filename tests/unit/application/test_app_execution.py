from __future__ import annotations

from pathlib import Path

import pytest
from orchestration_support import CALC_SOURCE, CoverageModel, FakeRunner, analyze, write_project

from pytestgen.application.execution import SuiteExecutor
from pytestgen.domain.models import (
    CaseOrigin,
    DropReason,
    FreeFormTest,
    ModuleInfo,
    Observation,
    ObservationKind,
    TestCaseSpec,
    TestSuite,
)
from pytestgen.errors import PytestgenError

FILE = "test_calc_pytestgen.py"


@pytest.fixture
def module(tmp_path: Path) -> ModuleInfo:
    return analyze(tmp_path, write_project(tmp_path, CALC_SOURCE))


def make_executor(tmp_path: Path, module: ModuleInfo, runner: FakeRunner, **kw: object):
    return SuiteExecutor(
        runner,
        project_root=tmp_path,
        target=module.path,
        file_name=FILE,
        timeout_s=33,
        per_test_timeout_s=4,
        **kw,  # type: ignore[arg-type]
    )


def free(name: str, extra_import: str = "") -> FreeFormTest:
    body = f"{extra_import}\n\ndef {name}():\n    assert 1\n"
    return FreeFormTest(name, "grade", body.lstrip())


def test_run_sends_the_rendered_module_next_to_the_existing_tests(
    tmp_path: Path, module: ModuleInfo
) -> None:
    existing = (tmp_path / "tests" / "test_old.py",)
    runner = FakeRunner()
    executor = make_executor(tmp_path, module, runner, existing_tests=existing)
    suite = TestSuite(module, (), (free("test_a"),))
    execution = executor.run(suite)
    (request,) = runner.requests
    assert list(request.test_sources) == [FILE]
    assert "def test_a" in request.test_sources[FILE]
    assert request.existing_tests == existing
    assert (request.timeout_s, request.per_test_timeout_s) == (33, 4)
    assert (request.project_root, request.target) == (tmp_path, module.path)
    assert execution.suite is suite and execution.dropped == ()


def test_empty_suite_runs_only_the_existing_tests(tmp_path: Path, module: ModuleInfo) -> None:
    runner = FakeRunner()
    executor = make_executor(tmp_path, module, runner, existing_tests=(tmp_path / "t.py",))
    executor.run(TestSuite(module))
    assert runner.requests[0].test_sources == {}


def test_run_existing_is_skipped_without_existing_tests(tmp_path: Path, module: ModuleInfo) -> None:
    runner = FakeRunner()
    assert make_executor(tmp_path, module, runner).run_existing() is None
    assert runner.requests == []


def test_ignore_existing_tests_removes_them_from_later_runs(
    tmp_path: Path, module: ModuleInfo
) -> None:
    runner = FakeRunner()
    executor = make_executor(tmp_path, module, runner, existing_tests=(tmp_path / "t.py",))
    executor.ignore_existing_tests()
    executor.run(TestSuite(module, (), (free("test_a"),)))
    assert runner.requests[0].existing_tests == ()
    assert executor.existing_tests == ()


def test_free_tests_that_break_collection_are_found_by_bisection_and_dropped(
    tmp_path: Path, module: ModuleInfo
) -> None:
    runner = FakeRunner(
        collection_error=lambda text: (
            "ImportError: cannot import name 'phantom' from 'calc'" if "phantom" in text else None
        )
    )
    bad = free("test_bad", "from calc import phantom")
    good = (free("test_g1"), free("test_g2"), free("test_g3"), free("test_g4"))
    suite = TestSuite(module, (), (good[0], good[1], bad, good[2], good[3]))
    execution = make_executor(tmp_path, module, runner).run(suite)
    assert execution.suite.free_tests == good
    (dropped,) = execution.dropped
    assert (dropped.name, dropped.reason) == ("test_bad", DropReason.INVALID_CODE)
    assert "cannot import name 'phantom'" in dropped.detail
    assert execution.result.collection_error is None
    assert len(runner.requests) < 2 + 2 * len(suite.free_tests), "bisection, not one run each"


def test_several_broken_free_tests_are_all_found(tmp_path: Path, module: ModuleInfo) -> None:
    runner = FakeRunner(collection_error=lambda text: "bad" if "phantom" in text else None)
    suite = TestSuite(
        module,
        (),
        (
            free("test_a", "from calc import phantom"),
            free("test_b"),
            free("test_c", "from calc import phantom"),
        ),
    )
    execution = make_executor(tmp_path, module, runner).run(suite)
    assert [t.name for t in execution.suite.free_tests] == ["test_b"]
    assert {d.name for d in execution.dropped} == {"test_a", "test_c"}


def test_collection_error_that_free_tests_do_not_explain_is_fatal(
    tmp_path: Path, module: ModuleInfo
) -> None:
    runner = FakeRunner(
        collection_error=lambda text: (
            "SyntaxError in the generated module" if "parametrize" in text else None
        )
    )
    obs = Observation({"score": 1}, ObservationKind.RETURNED, value_repr="'F'")
    case = TestCaseSpec("grade", {"score": 1}, obs, CaseOrigin.Z3)
    suite = TestSuite(module, (case,), (free("test_a"),))
    with pytest.raises(PytestgenError, match="cannot be collected: SyntaxError"):
        make_executor(tmp_path, module, runner).run(suite)


def test_unrenderable_suite_raises_a_user_facing_error(tmp_path: Path, module: ModuleInfo) -> None:
    obs = Observation({}, ObservationKind.RETURNED, value_repr="1")
    stray = TestCaseSpec("no_such_function", {}, obs, CaseOrigin.Z3)
    with pytest.raises(PytestgenError, match="cannot render"):
        make_executor(tmp_path, module, FakeRunner()).run(TestSuite(module, (stray,)))


def test_run_plain_does_not_recover_from_collection_errors(
    tmp_path: Path, module: ModuleInfo
) -> None:
    runner = FakeRunner(
        CoverageModel({}), collection_error=lambda text: "broken" if "phantom" in text else None
    )
    suite = TestSuite(module, (), (free("test_a", "from calc import phantom"),))
    result = make_executor(tmp_path, module, runner).run_plain(suite)
    assert result.collection_error == "broken"
    assert len(runner.requests) == 1
