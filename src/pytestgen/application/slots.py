"""The LLM slot fillers in one import: oracle review, input proposals, free-form writing.

Each collaborator lives in its own module (:mod:`.oracle`, :mod:`.proposer`,
:mod:`.writer`); this module only keeps the original import path working.
"""

from __future__ import annotations

from pytestgen.application.collaborators import WrittenTests
from pytestgen.application.oracle import OracleReviewer
from pytestgen.application.proposer import InputProposer
from pytestgen.application.writer import FreeFormWriter

__all__ = ["FreeFormWriter", "InputProposer", "OracleReviewer", "WrittenTests"]
