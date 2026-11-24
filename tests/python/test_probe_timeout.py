"""#108: probe and channel-list reads bound total time and react to a gone client."""

import asyncio

import httpx
from fastapi.testclient import TestClient
from starlette.requests import Request

from lisca.web import app as webui


class _Stream:
    def __init__(self, chunks=(), status=200, delay=0.0):
        self.chunks = list(chunks)
        self.status_code = status
        self.delay = delay

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def aiter_bytes(self):
        if self.delay:
            await asyncio.sleep(self.delay)
        for chunk in self.chunks:
            yield chunk


class _Client:
    def __init__(self, streams):
        self._streams = list(streams)
        self._calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def stream(self, *_args, **_kwargs):
        stream = self._streams[min(self._calls, len(self._streams) - 1)]
        self._calls += 1
        return stream

    async def get(self, *_args, **_kwargs):
        await asyncio.sleep(5)


def _patch_client(monkeypatch, client):
    monkeypatch.setattr(httpx, "AsyncClient", lambda *_args, **_kwargs: client)


def test_probe_deadline_marks_timeout_and_does_not_cache(monkeypatch):
    monkeypatch.setattr(webui, "_PROBE_TOTAL_TIMEOUT_SEC", 0.05)
    monkeypatch.setattr(webui, "_PROBE_CACHE", {})
    _patch_client(monkeypatch, _Client([_Stream([b"x" * 1024], delay=1.0)]))

    with TestClient(webui.app, raise_server_exceptions=False) as client:
        response = client.get("/probe", params={"url": "https://media.test/slow.m3u8"})

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is False
    assert body["timed_out"] is True
    assert webui._PROBE_CACHE == {}


async def _disconnected(_self):
    return True


def test_probe_stops_when_the_client_is_gone(monkeypatch):
    monkeypatch.setattr(webui, "_PROBE_CACHE", {})
    monkeypatch.setattr(Request, "is_disconnected", _disconnected)

    with TestClient(webui.app, raise_server_exceptions=False) as client:
        response = client.get("/probe", params={"url": "https://media.test/live.m3u8"})

    body = response.json()
    assert body["ok"] is False
    assert body["cancelled"] is True
    assert webui._PROBE_CACHE == {}


def test_a_successful_probe_is_cached(monkeypatch):
    monkeypatch.setattr(webui, "_PROBE_CACHE", {})
    _patch_client(monkeypatch, _Client([
        _Stream([b"#EXTM3U\nsegment-1.ts\n"]),
        _Stream([b"y" * (1024 * 1024)]),
    ]))

    with TestClient(webui.app, raise_server_exceptions=False) as client:
        response = client.get("/probe", params={"url": "https://media.test/live.m3u8"})

    body = response.json()
    assert body["ok"] is True
    assert "timed_out" not in body and "cancelled" not in body
    assert webui._PROBE_CACHE["https://media.test/live.m3u8"][1]["ok"] is True


def test_channel_list_read_has_a_total_deadline(monkeypatch):
    monkeypatch.setattr(webui, "_CHANNELS_TOTAL_TIMEOUT_SEC", 0.05)
    _patch_client(monkeypatch, _Client([_Stream([])]))

    with TestClient(webui.app, raise_server_exceptions=False) as client:
        response = client.get("/channels", params={"url": "https://media.test/list.m3u"})

    assert response.status_code == 502
    assert "超时" in response.json()["detail"]
