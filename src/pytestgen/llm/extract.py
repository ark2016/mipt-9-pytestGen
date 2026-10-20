"""Pull code and JSON out of free-form model output.

The parsers themselves are application policy and live in
:mod:`pytestgen.application.extract`; they are re-exported here for the adapter's users.
"""

from __future__ import annotations

from pytestgen.application.extract import extract_code, extract_json

__all__ = ["extract_code", "extract_json"]
