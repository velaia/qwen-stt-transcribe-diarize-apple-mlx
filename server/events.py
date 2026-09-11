"""In-process pub/sub for server-sent events.

Worker threads (PrepWorker, JobWorker) publish from outside the asyncio event
loop; `publish` hops onto each subscriber's loop with `call_soon_threadsafe`
so the actual queue.put_nowait always runs on the loop that owns the queue.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()

    def subscribe(self, loop: asyncio.AbstractEventLoop) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subs.add((loop, q))
        return q

    def unsubscribe(self, loop: asyncio.AbstractEventLoop, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs.discard((loop, q))

    def publish(self, event: str, data: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for loop, q in subs:
            # loop already closed -> subscriber will be reaped on disconnect
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(q.put_nowait, (event, data))

    def job(self, item_id: str, *, status: str, stage: str | None,
            progress: float | None, error: str | None) -> None:
        self.publish("job", {
            "id": item_id, "status": status, "stage": stage,
            "progress": progress, "error": error,
        })

    def transcript(self, item_id: str, action: str) -> None:
        self.publish("transcript", {"id": item_id, "action": action})
