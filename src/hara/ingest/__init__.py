"""HARA ingest package — type aliases shared between pipelines.

OnConflict is defined here (and not in each pipeline) so the three
literal strings stay in lock-step. A drift between, say, the structured
pipeline accepting "overwrite" while the unstructured still expected
"replace" would be silent under MyPy/Pyright if each defined its own.
"""

from __future__ import annotations

from typing import Literal

OnConflict = Literal["skip", "replace", "append"]

__all__ = ["OnConflict"]
