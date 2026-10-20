"""Sandbox: runs untrusted project code and generated tests in throw-away workspaces.

Adapters for the ``SuiteRunner``, ``ExecutionRecorder`` and ``ModuleIntrospector`` ports,
plus the building blocks they share (workspace copy, scrubbed environment, process tree
control).
"""

from pytestgen.sandbox.env import build_sandbox_env
from pytestgen.sandbox.introspector import SubprocessIntrospector
from pytestgen.sandbox.process import ProcessResult, run_process
from pytestgen.sandbox.recorder import SubprocessExecutionRecorder
from pytestgen.sandbox.runner import SubprocessSuiteRunner
from pytestgen.sandbox.workspace import DEFAULT_IGNORES, Workspace

__all__ = [
    "DEFAULT_IGNORES",
    "ProcessResult",
    "SubprocessExecutionRecorder",
    "SubprocessIntrospector",
    "SubprocessSuiteRunner",
    "Workspace",
    "build_sandbox_env",
    "run_process",
]
