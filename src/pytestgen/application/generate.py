"""The ``generate`` use case: from a module to a test file and a coverage-gain report."""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass, replace
from pathlib import Path

from pytestgen.application.attribution import OriginAttribution
from pytestgen.application.budget import GenerationBudget
from pytestgen.application.collaborators import ProjectTestFiles, Repairer, UsageMeter
from pytestgen.application.coverage_delta import compute_delta
from pytestgen.application.execution import SuiteExecutor
from pytestgen.application.fill import SlotFiller
from pytestgen.application.outputs import check_writable, default_output, module_stem
from pytestgen.application.progress import StageAnnouncer
from pytestgen.application.selection import FailingFreeTest, SuiteSelector
from pytestgen.application.state import Filled, RunState
from pytestgen.application.targets import is_hybrid_target, select_functions
from pytestgen.application.texts import first_line, last_line
from pytestgen.codegen import map_test_nodes
from pytestgen.domain.models import (
    Arc,
    BranchTarget,
    FileCoverage,
    FreeFormTest,
    FunctionInfo,
    GenerationReport,
    ModuleInfo,
    RunResult,
    Strategy,
    SuspectedBug,
    TestOutcome,
    TestSuite,
)
from pytestgen.errors import ModuleImportError, PytestgenError, SandboxError
from pytestgen.log import get_logger
from pytestgen.ports import ModuleAnalyzer, ModuleIntrospector, Stage, SuiteRunner

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class GenerateRequest:
    """What the user asked for."""

    target: Path
    project_root: Path
    output: Path | None = None
    """Default: ``<project_root>/tests/test_<module>_pytestgen.py``."""
    functions: tuple[str, ...] = ()
    """Qualified names (or a class name for all its methods); empty means every function."""
    strategy: Strategy = Strategy.HYBRID
    minimize: bool = False
    existing_tests: tuple[Path, ...] | None = None
    """``None``: discover the project's tests that import the module."""
    dry_run: bool = False
    overwrite: bool = False


@dataclass(frozen=True, slots=True)
class _Services:
    """Collaborators and budgets shared by every step of a run."""

    filler: SlotFiller
    repairer: Repairer
    selector: SuiteSelector
    files: ProjectTestFiles
    budget: GenerationBudget
    announce: StageAnnouncer


class GenerateTests:
    """Runs the hybrid pipeline (or the pure-LLM one) on one module.

    Stages: analyze, baseline coverage of the existing tests, per function solver inputs
    and recorded behaviour with LLM slots (or LLM-written tests), a sandboxed run with
    repair of failing free-form tests, a gap loop on uncovered arcs, selection, and
    writing the test file plus the report.
    """

    def __init__(
        self,
        *,
        analyzer: ModuleAnalyzer,
        introspector: ModuleIntrospector,
        runner: SuiteRunner,
        files: ProjectTestFiles,
        filler: SlotFiller,
        repairer: Repairer,
        selector: SuiteSelector,
        budget: GenerationBudget | None = None,
        meter: UsageMeter | None = None,
        announce: StageAnnouncer | None = None,
    ) -> None:
        self._analyzer = analyzer
        self._introspector = introspector
        self._runner = runner
        self._meter = meter
        self._announce = announce or StageAnnouncer()
        self._services = _Services(
            filler=filler,
            repairer=repairer,
            selector=selector,
            files=files,
            budget=budget or GenerationBudget(),
            announce=self._announce,
        )

    def execute(self, request: GenerateRequest) -> GenerationReport:
        """Run the pipeline; raises ``PytestgenError`` when no verified result can be produced."""
        started = time.monotonic()
        svc = self._services
        root = request.project_root.resolve()
        module = self._analyze(request, root)
        functions = select_functions(module, request.functions)
        output = request.output or default_output(root, module.module_name)
        if not request.dry_run:
            check_writable(svc.files, output, overwrite=request.overwrite)
        executor = SuiteExecutor(
            self._runner,
            project_root=root,
            target=module.path,
            file_name=f"test_{module_stem(module.module_name)}_pytestgen.py",
            existing_tests=self._existing_tests(request, root, module, output),
            timeout_s=svc.budget.run_timeout_s,
            per_test_timeout_s=svc.budget.test_timeout_s,
        )
        run = _Run(
            svc,
            request,
            root=root,
            module=module,
            functions=functions,
            executor=executor,
            output=output,
        )
        report = run.execute(started)
        if self._meter is None:
            return report
        return replace(report, llm_usage=self._meter.usage, model=self._meter.model)

    # ------------------------------------------------------------------ steps

    def _analyze(self, request: GenerateRequest, root: Path) -> ModuleInfo:
        self._announce(Stage.ANALYZE, f"analysing {request.target.name}")
        module = self._analyzer.analyze(request.target, root)
        introspection = self._introspector.introspect(root, module)
        if not introspection.importable:
            detail = introspection.error or ""
            _log.warning("module_import_failed", module=module.module_name, detail=detail)
            reason = last_line(detail) or "unknown import error"
            raise ModuleImportError(f"cannot import {module.module_name}: {reason}")
        return module

    def _existing_tests(
        self, request: GenerateRequest, root: Path, module: ModuleInfo, output: Path
    ) -> tuple[Path, ...]:
        if request.existing_tests is not None:
            return tuple(path.resolve() for path in request.existing_tests)
        found = self._services.files.discover(root, module.module_name, output)
        _log.info("existing_tests_discovered", count=len(found))
        return found


def coverage_score(coverage: FileCoverage | None) -> int:
    """Monotone measure of how much a run executed (lines plus arcs)."""
    if coverage is None:
        return -1
    return len(coverage.executed_lines) + len(coverage.executed_arcs)


def _unverified(result: RunResult) -> bool:
    """A run that proves nothing: it timed out, or pytest produced neither tests nor coverage."""
    return result.timed_out or (result.coverage is None and not result.outcomes)


def _is_assertion_failure(outcome: TestOutcome) -> bool:
    text = f"{outcome.message}\n{outcome.traceback}"
    return outcome.message.lstrip().startswith("assert") or "AssertionError" in text


# --------------------------------------------------------------------------- one run


class _Run:
    """Phases of a single :meth:`GenerateTests.execute` call; the state lives in ``RunState``."""

    def __init__(
        self,
        services: _Services,
        request: GenerateRequest,
        *,
        root: Path,
        module: ModuleInfo,
        functions: tuple[FunctionInfo, ...],
        executor: SuiteExecutor,
        output: Path,
    ) -> None:
        self._svc = services
        self._request = request
        self._root = root
        self._module = module
        self._functions = functions
        self._executor = executor
        self._output = output
        self._state = RunState(TestSuite(module))
        self._baseline: RunResult | None = None
        self._before: FileCoverage | None = None
        self._coverage: FileCoverage | None = None

    def execute(self, started: float) -> GenerationReport:
        self._run_baseline()
        self._seed()
        if not self._stabilize():
            raise PytestgenError(self._unverified_message())
        self._fill_gaps()
        self._select()
        return self._finish(started)

    # ------------------------------------------------------------------ baseline

    def _run_baseline(self) -> None:
        self._svc.announce(Stage.BASELINE, "running the existing tests")
        result = self._executor.run_existing()
        if result is None:
            return
        if result.collection_error or result.timed_out:
            reason = result.collection_error or "the run timed out"
            _log.warning("existing_tests_unusable", reason=first_line(reason))
            self._executor.ignore_existing_tests()
            return
        self._baseline = result
        self._before = result.coverage
        self._coverage = result.coverage

    # ------------------------------------------------------------------ first generation

    def _seed(self) -> None:
        for function in self._functions:
            try:
                self._state.absorb(self._seed_function(function))
            except SandboxError as exc:
                _log.warning("function_skipped", function=function.qualname, error=str(exc))

    def _seed_function(self, function: FunctionInfo) -> Filled:
        filler = self._svc.filler
        if self._request.strategy is Strategy.HYBRID and is_hybrid_target(function):
            return filler.hybrid(self._root, self._module, function)
        return filler.free_form(self._module, function, avoid_names=self._state.free_names)

    # ------------------------------------------------------------------ run + repair

    def _stabilize(self) -> bool:
        """Run until nothing fails: failing cases are dropped, failing free tests repaired.

        Returns ``False`` when a run could not be verified (timeout, no results): the
        suite is then unproven and the caller must not trust ``self._coverage``.
        """
        state = self._state
        if not state.is_empty:
            self._svc.announce(Stage.RUN, "running the generated tests")
        while not state.is_empty:
            execution = self._executor.run(state.suite)
            state.apply_execution(execution)
            if _unverified(execution.result):
                _log.warning(
                    "suite_unverified",
                    timed_out=execution.result.timed_out,
                    tests=len(execution.result.outcomes),
                )
                return False
            selection = self._svc.selector.select(
                state.suite, execution.result, self._executor.file_name
            )
            state.apply_selection(selection)
            if selection.clean:
                self._coverage = execution.result.coverage
                return True
            for failing in selection.failing_free:
                self._repair(failing)
        # no tests, or all given up: nothing left to run, the baseline is all there is
        self._coverage = self._baseline.coverage if self._baseline else None
        return True

    def _unverified_message(self) -> str:
        budget = self._svc.budget
        return (
            f"the sandbox run did not finish (limit {budget.run_timeout_s:g}s), so the generated "
            "tests could not be verified and nothing was written; "
            "raise PYTESTGEN_RUN_TIMEOUT_S or narrow the run with --function"
        )

    def _repair(self, failing: FailingFreeTest) -> None:
        test, outcome = failing.test, failing.outcome
        reason = first_line(outcome.message or outcome.traceback)
        fixed = None
        if test.repair_attempts < self._svc.budget.repair_attempts:
            self._svc.announce(Stage.REPAIR, f"repairing {test.name}")
            fixed = self._try_repair(test, failing)
        if fixed is None:
            _log.info("test_given_up", test=test.name, reason=reason)
            self._state.give_up(test, reason)
            return
        self._state.replace_free(test, fixed)
        self._note_rewrite(test, outcome, reason)

    def _try_repair(self, test: FreeFormTest, failing: FailingFreeTest) -> FreeFormTest | None:
        function = self._function_or_none(test.function)
        fixed = self._svc.repairer.repair(self._module, function, test, failing.outcome)
        if fixed is not None and fixed.repair_attempts <= test.repair_attempts:
            # the loop must end even if a repairer forgets to count its attempt
            fixed = replace(fixed, repair_attempts=test.repair_attempts + 1)
        return fixed

    def _note_rewrite(self, test: FreeFormTest, outcome: TestOutcome, reason: str) -> None:
        """Flag a test that was bent to the code's behaviour, so a masked bug can be spotted.

        The code under test is the source of truth for repairs, but when the function
        documents its contract and an *assertion* failed, the original expectation may have
        been the right one.
        """
        function = self._function_or_none(test.function)
        if function is None or not function.docstring or not _is_assertion_failure(outcome):
            return
        _log.warning("test_rewritten_to_match_code", test=test.name, function=test.function)
        self._state.note_rewrite(
            test.name,
            SuspectedBug(
                function=test.function,
                inputs={"test": test.name},
                observed=reason,
                explanation=(
                    "the test failed as first written and was rewritten to match the current "
                    f"behaviour; if the docstring of {test.function} supports the original "
                    "expectation, the code has a bug"
                ),
            ),
        )

    def _function_or_none(self, qualname: str) -> FunctionInfo | None:
        try:
            return self._module.function(qualname)
        except KeyError:
            return None

    # ------------------------------------------------------------------ gap loop

    def _fill_gaps(self) -> None:
        for iteration in range(1, self._svc.budget.gap_iterations + 1):
            coverage = self._coverage
            if coverage is None:
                return
            if not any(self._has_gap(fn, coverage) for fn in self._functions):
                return
            self._svc.announce(Stage.GAP, f"gap iteration {iteration}: uncovered code")
            checkpoint = self._state.checkpoint()
            if not self._fill_once(coverage):
                return
            verified = self._stabilize()
            if not verified or coverage_score(self._coverage) <= coverage_score(coverage):
                _log.info("gap_iteration_no_gain", iteration=iteration, verified=verified)
                self._state.rollback(checkpoint)
                self._coverage = coverage
                return

    def _has_gap(self, function: FunctionInfo, coverage: FileCoverage) -> bool:
        return any(function.contains_line(n) for n in coverage.missing_lines) or bool(
            self._fresh_targets(function, coverage.missing_arcs)
        )

    def _fill_once(self, coverage: FileCoverage) -> bool:
        """Add tests aimed at what ``coverage`` misses; whether anything was added."""
        added = False
        for function in self._functions:
            uncovered = sorted(n for n in coverage.missing_lines if function.contains_line(n))
            filled = self._aim_at_arcs(function, coverage.missing_arcs)
            if not filled.adds_tests and uncovered:
                filled += self._svc.filler.free_form(
                    self._module,
                    function,
                    uncovered_lines=uncovered,
                    avoid_names=self._state.free_names,
                )
            added = added or filled.adds_tests
            self._state.absorb(filled)
        return added

    def _aim_at_arcs(self, function: FunctionInfo, missing: Collection[Arc]) -> Filled:
        if self._request.strategy is not Strategy.HYBRID or not is_hybrid_target(function):
            return Filled()
        targets = self._fresh_targets(function, missing)
        if not targets:
            return Filled()
        return self._svc.filler.refine(
            self._root,
            self._module,
            function,
            targets,
            known=self._state.known_vectors(function.qualname),
        )

    def _fresh_targets(
        self, function: FunctionInfo, missing: Collection[Arc]
    ) -> list[BranchTarget]:
        skip = self._state.attempted | self._state.infeasible
        return [t for t in function.branches if t.arc in missing and t.key not in skip]

    # ------------------------------------------------------------------ minimize

    def _select(self) -> None:
        if not self._request.minimize:
            self._svc.announce(Stage.SELECT, "keeping every passing test")
            return
        self._svc.announce(Stage.SELECT, "dropping tests that add no coverage")
        result = self._svc.selector.minimize(
            self._state.suite,
            self._coverage,
            self._executor.run_plain,
            self._executor.file_name,
        )
        self._state.apply_minimized(result)
        if result.coverage is not None:
            self._coverage = result.coverage

    # ------------------------------------------------------------------ result

    def _finish(self, started: float) -> GenerationReport:
        self._svc.announce(Stage.WRITE, "writing the test file")
        suite = self._state.suite
        nodes = map_test_nodes(suite)
        written: Path | None = None
        if nodes and not self._request.dry_run:
            written = self._svc.files.write(self._output, self._executor.render(suite))
        elif not nodes:
            _log.warning("nothing_to_write", module=self._module.module_name)
        after = self._coverage if self._coverage is not None else self._before
        if after is None:
            _log.warning("coverage_unavailable", module=self._module.module_name)
        counts = Counter(item.origin for item in nodes.values())
        report = GenerationReport(
            module=self._module.module_name,
            strategy=self._request.strategy,
            output_path=written,
            delta=compute_delta(self._module, self._before, after),
            kept_tests=len(nodes),
            dropped=self._state.dropped,
            suspected_bugs=self._state.bugs,
            by_origin=OriginAttribution(self._executor.run_plain).stats(
                suite, counts, self._before, after
            ),
            duration_s=time.monotonic() - started,
        )
        _log.info(
            "generation_finished",
            module=report.module,
            kept=report.kept_tests,
            dropped=len(report.dropped),
            bugs=len(report.suspected_bugs),
        )
        return report


__all__ = [
    "GenerateRequest",
    "GenerateTests",
    "check_writable",
    "default_output",
    "select_functions",
]
