"""Use cases of pytestgen: orchestration of the pipeline and the LLM collaborators."""

from __future__ import annotations

from pytestgen.application.analyze import (
    AnalyzeModule,
    AnalyzeRequest,
    BranchAnalysis,
    FunctionAnalysis,
    ModuleAnalysis,
)
from pytestgen.application.availability import CircuitBreakerLLMClient
from pytestgen.application.budget import GenerationBudget
from pytestgen.application.fill import SlotFiller
from pytestgen.application.generate import GenerateRequest, GenerateTests
from pytestgen.application.guard import check_test_source
from pytestgen.application.oracle import OracleReviewer
from pytestgen.application.progress import StageAnnouncer
from pytestgen.application.project import find_project_root
from pytestgen.application.proposer import InputProposer
from pytestgen.application.repair import TestRepairer
from pytestgen.application.selection import SuiteSelector
from pytestgen.application.skeleton import SkeletonBuilder
from pytestgen.application.targets import select_functions
from pytestgen.application.writer import FreeFormWriter

__all__ = [
    "AnalyzeModule",
    "AnalyzeRequest",
    "BranchAnalysis",
    "CircuitBreakerLLMClient",
    "FreeFormWriter",
    "FunctionAnalysis",
    "GenerateRequest",
    "GenerateTests",
    "GenerationBudget",
    "InputProposer",
    "ModuleAnalysis",
    "OracleReviewer",
    "SkeletonBuilder",
    "SlotFiller",
    "StageAnnouncer",
    "SuiteSelector",
    "TestRepairer",
    "check_test_source",
    "find_project_root",
    "select_functions",
]
