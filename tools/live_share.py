"""State and capability URLs for one read-only LAN live share."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import threading
import time
from collections import deque


MAX_VIEWERS = 3
VIEWER_RESERVATION_SECONDS = 60


class LiveShare:
    def __init__(self, url: str):
        self.token = secrets.token_urlsafe(24)
        self.url = url
        self.active = True
        self.stop_event = threading.Event()
        self._lock = threading.RLock()
        # A join reservation occupies a viewer slot before the browser requests
        # media. Reservations expire if the client never opens its WebSocket.
        self._viewers: dict[str, tuple[asyncio.Queue | None, float, str]] = {}
        self._events: deque[dict] = deque(maxlen=240)
        self._timeline: dict | None = None
        self._ready: dict | None = None
        self._buffer: dict | None = None
        self._media: dict[str, tuple[str, str]] = {}

    @property
    def viewer_count(self) -> int:
        with self._lock:
            self._expire_viewer_reservations()
            return len(self._viewers)

    def _expire_viewer_reservations(self) -> None:
        now = time.monotonic()
        expired = [viewer_id for viewer_id, (queue, expires_at, _client_host)
                   in self._viewers.items()
                   if queue is None and expires_at <= now]
        for viewer_id in expired:
            self._viewers.pop(viewer_id, None)

    def reserve_viewer(self, client_host: str) -> str | None:
        with self._lock:
            if not self.active:
                return None
            self._expire_viewer_reservations()
            if len(self._viewers) >= MAX_VIEWERS:
                return None
            viewer_id = secrets.token_urlsafe(24)
            self._viewers[viewer_id] = (
                None, time.monotonic() + VIEWER_RESERVATION_SECONDS, client_host
            )
            return viewer_id

    def has_viewer(self, viewer_id: str, client_host: str) -> bool:
        with self._lock:
            self._expire_viewer_reservations()
            reservation = self._viewers.get(viewer_id)
            return (self.active and reservation is not None
                    and reservation[2] == client_host)

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
            elif kind in ("subtitle", "caption_remove", "preview"):
                self._events.append(event)
            elif kind in ("error", "end"):
                self._events.append(event)
            for queue, _expires_at, _client_host in tuple(self._viewers.values()):
                if queue is None:
                    continue
                if queue.full():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass

    def subscribe(self, viewer_id: str, client_host: str
                  ) -> tuple[asyncio.Queue, list[dict]] | None:
        with self._lock:
            self._expire_viewer_reservations()
            reservation = self._viewers.get(viewer_id)
            if (not self.active or reservation is None or reservation[0] is not None
                    or reservation[2] != client_host):
                return None
            queue: asyncio.Queue = asyncio.Queue(maxsize=256)
            self._viewers[viewer_id] = (queue, float("inf"), client_host)
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

    def unsubscribe(self, viewer_id: str) -> None:
        with self._lock:
            self._viewers.pop(viewer_id, None)

    def stop(self) -> None:
        self.stop_event.set()
        self.publish({"type": "end", "reason": "cancelled"})
        self.active = False
