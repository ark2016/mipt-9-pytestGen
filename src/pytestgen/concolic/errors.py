"""Errors of the concolic package."""

from __future__ import annotations

from pytestgen.errors import PytestgenError


class UnsupportedExpression(PytestgenError):  # noqa: N818 - a reason, not a failure
    """A condition (or a parameter) uses something the z3 translation cannot express.

    The message is a human-readable reason; it ends up in ``Solution.reason``.
    """
