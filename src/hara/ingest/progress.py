"""Progress events emitted by ingest pipelines for the CLI to render.

Both pipelines (structured / unstructured) call ``callback(event)`` at key
points — file start, stage transitions, completion. The CLI renders these
via Rich; tests use a no-op callback or capture for assertions.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

Pipeline = Literal["sql", "vector"]
Stage = Literal[
    "start",  # file dispatched
    "parsing",  # parser starting (PDFs/docx — slow)
    "chunking",  # chunking starting
    "upserting",  # connector upsert starting
    "done",  # success
    "skipped",  # dedup or collision
    "failed",  # error path
]


@dataclass(frozen=True)
class ProgressEvent:
    """One progress event from a pipeline.

    `index` is 1-based file index within the pipeline's filtered file list.
    `detail` is an optional human-readable detail string (e.g. "28 rows",
    "1063 chunks", or an error message).
    """

    pipeline: Pipeline
    file: str  # relative_path
    index: int  # 1-based
    total: int
    stage: Stage
    detail: str = ""


ProgressCallback = Callable[[ProgressEvent], None]


def noop(_event: ProgressEvent) -> None:
    """Default callback for pipelines: silent."""
