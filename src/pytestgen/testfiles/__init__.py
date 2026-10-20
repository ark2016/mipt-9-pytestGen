"""File-system adapter: where generated tests are written and existing ones are found."""

from __future__ import annotations

from pytestgen.testfiles.discovery import discover_existing_tests, is_generated
from pytestgen.testfiles.store import LocalTestFiles

__all__ = ["LocalTestFiles", "discover_existing_tests", "is_generated"]
