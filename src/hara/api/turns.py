"""TurnRegistry — process-local map of turn_id → asyncio.Task.

Spec §6 "Single-worker requirement no v0.1": each turn runs as an
``asyncio.Task`` inside the FastAPI process. ``DELETE
/threads/{id}/turns/{id}`` calls ``Task.cancel()``; ``CancelledError``
propagates through ``Orchestrator.run_turn``.

This is process-bound state — it's why ``hara serve`` forces
``--workers=1``. Plano 5 may add a heartbeat-based reaper to allow
multi-worker.
"""

from __future__ import annotations

import asyncio
from typing import Any


class TurnRegistry:
    """Tracks in-flight turn tasks. Not thread-safe — single asyncio loop."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    def register(self, turn_id: str, task: asyncio.Task[Any]) -> None:
        self._tasks[turn_id] = task

    def unregister(self, turn_id: str) -> None:
        self._tasks.pop(turn_id, None)

    def get(self, turn_id: str) -> asyncio.Task[Any] | None:
        return self._tasks.get(turn_id)

    def cancel(self, turn_id: str) -> bool:
        """Cancel ``turn_id``'s task if still running. Returns True if cancel
        was issued, False if no such turn or already done."""
        task = self._tasks.get(turn_id)
        if task is None:
            return False
        if task.done():
            return False
        task.cancel()
        return True

    def cancel_all(self) -> int:
        """Cancel every still-running task; return how many were cancelled.

        Used at API shutdown so in-flight turns terminate promptly instead
        of leaking past the lifespan.
        """
        n = 0
        for task in list(self._tasks.values()):
            if not task.done():
                task.cancel()
                n += 1
        return n
