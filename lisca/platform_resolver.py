"""Bounded, source-keyed resolution of platform page URLs."""

import asyncio
import subprocess
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor


_CACHE_TTL_SECONDS = 300
_CACHE_MAX_ENTRIES = 256
# Keep no more than four expensive yt-dlp processes active across all requests.
_RESOLVER_POOL = ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="platform-resolver"
)
_LOCK = threading.RLock()
_CACHE: OrderedDict[tuple[str, str], tuple[float, str]] = OrderedDict()
_IN_FLIGHT: dict[tuple[str, str], Future] = {}


def _resolve_with_ytdlp(user_input: str, media_format: str) -> str:
    """Run the external resolver synchronously on a bounded worker thread."""
    proc = None
    for _attempt in range(2):
        proc = subprocess.run(
            ["yt-dlp", "-g", "--no-warnings", "-f", media_format, user_input],
            capture_output=True,
            text=True,
            timeout=120,
        )
        urls = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        if proc.returncode == 0 and urls:
            return urls[0]
    raise ValueError(f"流解析失败({media_format}): {proc.stderr.strip()[:200]}")


def _cache_completed_resolution(
    key: tuple[str, str], future: Future
) -> None:
    with _LOCK:
        if _IN_FLIGHT.get(key) is future:
            del _IN_FLIGHT[key]
        try:
            result = future.result()
        except Exception:
            return
        _CACHE[key] = (time.monotonic() + _CACHE_TTL_SECONDS, result)
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_MAX_ENTRIES:
            _CACHE.popitem(last=False)


async def resolve_platform_url(user_input: str, media_format: str) -> str:
    """Resolve and cache one source/format pair without blocking the event loop.

    Concurrent requests for the same pair share one resolver process. Distinct
    pairs retain separate cache entries, and the worker pool bounds active
    resolver processes to four.
    """
    key = (user_input, media_format)
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            expires_at, result = cached
            if expires_at > now:
                _CACHE.move_to_end(key)
                return result
            del _CACHE[key]

        future = _IN_FLIGHT.get(key)
        if future is None:
            future = _RESOLVER_POOL.submit(
                _resolve_with_ytdlp, user_input, media_format
            )
            _IN_FLIGHT[key] = future
            future.add_done_callback(
                lambda completed: _cache_completed_resolution(key, completed)
            )

    return await asyncio.shield(asyncio.wrap_future(future))
