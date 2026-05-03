"""ApiTurnRunner — runs a turn under the FastAPI process.

Wraps :class:`Orchestrator.run_turn` with the side effects the API needs:

- Persists every event via :meth:`SessionStore.record_event`.
- Publishes each event on :class:`EventBus` for live SSE subscribers.
- Persists the final ``TurnResult`` row when the turn completes.
- On cancellation, marks the turn ``status='canceled'`` in DB.

The runner is created per-turn and registered on :class:`TurnRegistry`
under the turn_id; the route handler returns 202 immediately and the
runner finishes async.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Any

from hara.agent.orchestrator import Orchestrator
from hara.api.events import EventBus
from hara.services.session_store import SessionStore

log = logging.getLogger(__name__)


class ApiTurnRunner:
    """Bridge between Orchestrator and the API's persistence + pub-sub layers.

    One instance per turn — created by the route handler and dropped after
    :meth:`run` returns. Holds no per-turn state of its own beyond the
    references to the shared store/bus/orchestrator.
    """

    def __init__(
        self,
        *,
        orchestrator: Orchestrator,
        store: SessionStore,
        bus: EventBus,
    ) -> None:
        self._orch = orchestrator
        self._store = store
        self._bus = bus

    async def run(
        self,
        *,
        thread_id: str,
        question: str,
        turn_id: str,
    ) -> None:
        """Execute the turn end-to-end. Updates DB; publishes events."""

        async def _on_event(event: dict[str, Any]) -> None:
            # Persist FIRST so SSE replay always sees this event before
            # any live subscriber observes it on the bus — keeps the
            # replay-then-tail invariant noted in events.py.
            try:
                seq = await self._store.record_event(
                    turn_id=turn_id,
                    event_type=event["event"],
                    data=event["data"],
                )
            except Exception:
                log.exception("record_event failed for %s", turn_id)
                return
            # Attach the DB ``seq`` to the published event so the SSE
            # generator can dedupe queue messages against the replay's
            # max seq when a subscriber races subscribe→replay.
            await self._bus.publish(turn_id, {**event, "seq": seq})

        try:
            result = await self._orch.run_turn(
                question,
                thread_id=thread_id,
                turn_id=turn_id,
                on_event=_on_event,
            )
        except asyncio.CancelledError:
            await self._store.update_turn_status(
                turn_id=turn_id,
                status="canceled",
            )
            await self._bus.close_turn(turn_id)
            raise
        except Exception as e:
            log.exception("turn %s failed: %s", turn_id, e)
            await self._store.update_turn_status(
                turn_id=turn_id,
                status="failed",
            )
            await self._bus.close_turn(turn_id)
            return

        # Final + done events. ``final`` carries the same shape as
        # InvokeResponse so /invoke can derive its sync response from the
        # last ``final`` event without a second DB roundtrip.
        final_payload: dict[str, Any] = {
            "turn_id": result.turn_id,
            "thread_id": result.thread_id,
            "answer": result.answer,
            "citations": result.citations,
            "metadata": result.metadata,
        }
        await _on_event({"event": "final", "data": final_payload})
        await _on_event({"event": "done", "data": {}})
        await self._bus.close_turn(turn_id)


def new_turn_id() -> str:
    return f"trn_{secrets.token_urlsafe(12)}"
