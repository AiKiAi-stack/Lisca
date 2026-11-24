"""Local, append-only operation records (#111).

Each user action, API request and session lifecycle event is appended to
``logs/ops-YYYYMMDD.jsonl`` so a report can be traced back from the same
``session_id``. Display and export redact URL credentials/query values by
default; the on-disk copy keeps them because ``logs/`` is local data.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_OPS_FILE = re.compile(r"^ops-(\d{8})\.jsonl$")
_URL = re.compile(r"(?:https?|rtsp|rtmp|rtp)://[^\s\"'<>]+", re.I)
_REDACTED_KEYS = {"ts", "session", "req_id", "action", "kind", "method", "path", "status"}


def _redact_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        if not parts.scheme or not parts.netloc:
            return value
        host = parts.netloc.rsplit("@", 1)[-1]
        query = urlencode([(key, "[已隐藏]") for key, _ in parse_qsl(
            parts.query, keep_blank_values=True)])
        return urlunsplit((parts.scheme, host, parts.path, query, ""))
    except ValueError:
        return "[已隐藏 URL]"


def redact_text(value: str) -> str:
    """Hide credentials and query values in any URL inside free text."""
    return _URL.sub(lambda match: _redact_url(match.group(0)), str(value))


def redact_entry(entry: dict) -> dict:
    result = {}
    for key, value in entry.items():
        if isinstance(value, str) and key not in _REDACTED_KEYS:
            result[key] = redact_text(value)
        else:
            result[key] = value
    return result


class OpsLog:
    """Thread-safe JSONL store with daily rotation and bounded retention."""

    def __init__(self, directory, *, max_age_days: int = 7,
                 max_bytes: int = 50 * 1024 * 1024):
        self.directory = Path(directory)
        self.max_age_days = max_age_days
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._last_prune = 0.0

    def path_for(self, now: datetime | None = None) -> Path:
        stamp = (now or datetime.now()).strftime("%Y%m%d")
        return self.directory / f"ops-{stamp}.jsonl"

    def append(self, entry: dict, now: datetime | None = None) -> dict:
        entry = dict(entry)
        entry.setdefault("ts", datetime.now(timezone.utc).isoformat(
            timespec="milliseconds").replace("+00:00", "Z"))
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.path_for(now)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            self._prune_if_due(now or datetime.now())
        return entry

    def files(self) -> list[Path]:
        if not self.directory.is_dir():
            return []
        return sorted(path for path in self.directory.glob("ops-*.jsonl")
                      if _OPS_FILE.match(path.name))

    def _prune_if_due(self, now: datetime) -> None:
        if time.time() - self._last_prune < 3600:
            return
        self._last_prune = time.time()
        self.prune(now)

    def prune(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        cutoff = now - timedelta(days=self.max_age_days)
        for path in self.files():
            try:
                modified = datetime.fromtimestamp(path.stat().st_mtime)
            except OSError:
                continue
            if modified < cutoff:
                path.unlink(missing_ok=True)
        files = self.files()
        total = sum(path.stat().st_size for path in files if path.exists())
        for path in files:
            if total <= self.max_bytes:
                break
            try:
                size = path.stat().st_size
            except OSError:
                continue
            path.unlink(missing_ok=True)
            total -= size

    def read(self, session: str | None = None, limit: int = 500) -> list[dict]:
        events: list[dict] = []
        with self._lock:
            for path in self.files():
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except OSError:
                    continue
                for line in lines:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if session and event.get("session") != session:
                        continue
                    events.append(event)
        events.sort(key=lambda event: str(event.get("ts", "")))
        return events[-limit:]

    def export(self, session: str | None = None, full: bool = False) -> str:
        events = self.read(session=session, limit=10 ** 9)
        if not full:
            events = [redact_entry(event) for event in events]
        return "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events)
