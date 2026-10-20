"""Coverage delta: how much a generated suite added on top of the baseline.

The computation is pure arithmetic over domain types and lives in
:mod:`pytestgen.application.coverage_delta`; it is re-exported for the report package.
"""

from __future__ import annotations

from pytestgen.application.coverage_delta import compute_delta

__all__ = ["compute_delta"]
