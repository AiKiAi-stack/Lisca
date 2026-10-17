"""State and capability URLs for one read-only LAN live share."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import threading
from collections import deque


MAX_VIEWERS = 3


class LiveShare:
    def __init__(self, url: str, engine: str, silence: str, delay: str):
        self.token = secrets.token_urlsafe(24)
        self.url = url
        self.engine = engine
        self.silence = silence
        self.delay = delay
        self.active = True
        self.stop_event = threading.Event()
        self._lock = threading.RLock()
        self._viewers: set[asyncio.Queue] = set()
        self._events: deque[dict] = deque(maxlen=240)
        self._timeline: dict | None = None
        self._ready: dict | None = None
        self._buffer: dict | None = None
        self._media: dict[str, tuple[str, str]] = {}

    @property
    def viewer_count(self) -> int:
        with self._lock:
            return len(self._viewers)

    def register_media(self, url: str, role: str = "video") -> str:
        """Return an opaque, session-scoped id for a manifest-discovered URL."""
        media_id = hashlib.sha256(f"{role}\0{url}".encode("utf-8")).hexdigest()[:32]
        with self._lock:
            self._media[media_id] = (url, role)
            while len(self._media) > 3000:
                self._media.pop(next(iter(self._media)))
        return media_id

    def media_url(self, media_id: str) -> tuple[str, str] | None:
        with self._lock:
            return self._media.get(media_id)

    def publish(self, event: dict) -> None:
        if not self.active:
            return
        with self._lock:
            kind = event.get("type")
            if kind == "timeline":
                self._timeline = event
            elif kind == "ready":
                self._ready = event
            elif kind == "buffer":
                self._buffer = event
            elif kind in ("subtitle", "preview"):
                self._events.append(event)
            elif kind in ("error", "end"):
                self._events.append(event)
            for queue in tuple(self._viewers):
                if queue.full():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass

    def subscribe(self) -> tuple[asyncio.Queue, list[dict]] | None:
        with self._lock:
            if not self.active or len(self._viewers) >= MAX_VIEWERS:
                return None
            queue: asyncio.Queue = asyncio.Queue(maxsize=256)
            self._viewers.add(queue)
            snapshot = []
            if self._timeline:
                snapshot.append(self._timeline)
            snapshot.extend(self._events)
            if self._ready:
                # A joining device starts at its own current HLS live edge.
                # Do not seek it back to this session's original start time.
                snapshot.append({**self._ready, "start_at": None})
            if self._buffer:
                snapshot.append(self._buffer)
            return queue, snapshot

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._viewers.discard(queue)

    def stop(self) -> None:
        self.stop_event.set()
        self.publish({"type": "end"})
        self.active = False
