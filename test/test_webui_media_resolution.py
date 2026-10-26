"""Public HTTP regressions for platform media resolution."""

import asyncio
import os
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter

import httpx
import pytest
from fastapi import Response
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import webui  # noqa: E402


def _page_url(label):
    return f"https://www.bilibili.com/video/BVopt2-{label}-{uuid.uuid4().hex}"


def _resolved_url(source, media_format):
    source_id = source.rsplit("/", 1)[-1]
    format_id = "video" if media_format == "bv*" else "audio"
    return f"https://media.example/{source_id}/{format_id}.m3u8"


def _completed_process(args, media_format, source):
    return subprocess.CompletedProcess(
        args, 0, stdout=_resolved_url(source, media_format) + "\n", stderr=""
    )


@pytest.mark.parametrize("route", ["video", "audio", "shared-media"])
def test_slow_platform_resolution_does_not_stall_other_http_or_event_loop(
    monkeypatch, route
):
    source = _page_url(route)
    resolver_started = threading.Event()
    release_resolver = threading.Event()
    resolver_finished = threading.Event()
    resolver_times = {}
    home_times = {}
    heartbeat_times = []

    def slow_run(args, **_kwargs):
        media_format, resolved_source = args[-2:]
        resolver_times["started"] = time.monotonic()
        resolver_started.set()
        assert release_resolver.wait(timeout=2)
        resolver_times["finished"] = time.monotonic()
        resolver_finished.set()
        return _completed_process(args, media_format, resolved_source)

    async def proxy(request, url, **_kwargs):
        del request
        return Response(url, media_type="text/plain")

    monkeypatch.setattr(webui, "_lan_ipv4_candidates", lambda: ["192.168.1.2"])
    monkeypatch.setattr(webui, "_proxy_stream", proxy)
    monkeypatch.setattr(subprocess, "run", slow_run)

    async def exercise():
        transport = httpx.ASGITransport(app=webui.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            if route == "shared-media":
                started = await client.post("/share/start", json={"url": source})
                assert started.status_code == 200, started.text
                token = started.json()["token"]
                joined = await client.post(f"/share/{token}/join")
                assert joined.status_code == 200, joined.text
                media_path = joined.json()["video"]
            else:
                token = None
                media_path = f"/{route}"

            request_url = (
                media_path if route == "shared-media"
                else f"{media_path}?url={source}"
            )
            release_timer = threading.Timer(0.45, release_resolver.set)
            release_timer.start()
            heartbeat_stop = asyncio.Event()

            async def heartbeat():
                while not heartbeat_stop.is_set():
                    if resolver_started.is_set() and not resolver_finished.is_set():
                        heartbeat_times.append(time.monotonic())
                    await asyncio.sleep(0.01)

            async def get_home():
                response = await client.get("/")
                home_times["finished"] = time.monotonic()
                return response

            heartbeat_task = asyncio.create_task(heartbeat())
            media_task = asyncio.create_task(client.get(request_url))
            home_task = asyncio.create_task(get_home())
            try:
                media_response, home_response = await asyncio.gather(
                    media_task, home_task
                )
            finally:
                heartbeat_stop.set()
                await heartbeat_task
                release_resolver.set()
                release_timer.cancel()
                if token:
                    await client.post(f"/share/stop/{token}")

            return media_response, home_response

    media_response, home_response = asyncio.run(exercise())

    assert media_response.status_code == 200, media_response.text
    assert media_response.text == _resolved_url(
        source, "ba" if route == "audio" else "bv*"
    )
    assert home_response.status_code == 200
    assert home_times["finished"] < resolver_times["finished"]
    assert any(
        resolver_times["started"] < tick < resolver_times["finished"]
        for tick in heartbeat_times
    )


def test_concurrent_platform_results_keep_source_and_format_cache_keys(monkeypatch):
    source_a = _page_url("cache-a")
    source_b = _page_url("cache-b")
    calls = Counter()
    calls_lock = threading.Lock()
    active_resolvers = 0
    max_active_resolvers = 0
    all_sources_started = threading.Event()

    def controlled_run(args, **_kwargs):
        nonlocal active_resolvers, max_active_resolvers
        media_format, source = args[-2:]
        key = (source, media_format)
        with calls_lock:
            calls[key] += 1
            active_resolvers += 1
            max_active_resolvers = max(max_active_resolvers, active_resolvers)
            if active_resolvers >= 4:
                all_sources_started.set()
        try:
            all_sources_started.wait(timeout=0.2)
            return _completed_process(args, media_format, source)
        finally:
            with calls_lock:
                active_resolvers -= 1

    async def proxy(request, url, **_kwargs):
        del request
        return Response(url, media_type="text/plain")

    monkeypatch.setattr(webui, "_proxy_stream", proxy)
    monkeypatch.setattr(subprocess, "run", controlled_run)

    async def exercise():
        transport = httpx.ASGITransport(app=webui.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            requests = [
                ("video", source_a),
                ("video", source_a),
                ("audio", source_a),
                ("video", source_b),
                ("audio", source_b),
            ]

            async def fetch(route, source):
                response = await client.get(f"/{route}", params={"url": source})
                return response, route, source

            initial = await asyncio.gather(
                *(fetch(route, source) for route, source in requests)
            )
            cached = []
            for route, source in (
                ("video", source_a),
                ("audio", source_a),
                ("video", source_b),
                ("audio", source_b),
            ):
                cached.append(await fetch(route, source))
            return initial, cached

    initial, cached = asyncio.run(exercise())

    expected_keys = {
        (source_a, "bv*"),
        (source_a, "ba"),
        (source_b, "bv*"),
        (source_b, "ba"),
    }
    assert {key for key, count in calls.items() if count} == expected_keys
    assert all(count == 1 for count in calls.values())
    assert max_active_resolvers >= 2
    for response, route, source in initial + cached:
        media_format = "bv*" if route == "video" else "ba"
        assert response.status_code == 200, response.text
        assert response.text == _resolved_url(source, media_format)


def test_platform_resolution_caps_processes_and_coalesces_duplicate_requests(
    monkeypatch,
):
    sources = [_page_url(f"bounded-{index}") for index in range(5)]
    calls = Counter()
    calls_lock = threading.Lock()
    active_resolvers = 0
    max_active_resolvers = 0
    release_resolvers = threading.Event()

    def controlled_run(args, **_kwargs):
        nonlocal active_resolvers, max_active_resolvers
        media_format, source = args[-2:]
        key = (source, media_format)
        with calls_lock:
            calls[key] += 1
            active_resolvers += 1
            max_active_resolvers = max(max_active_resolvers, active_resolvers)
        try:
            assert release_resolvers.wait(timeout=2)
            return _completed_process(args, media_format, source)
        finally:
            with calls_lock:
                active_resolvers -= 1

    async def proxy(request, url, **_kwargs):
        del request
        return Response(url, media_type="text/plain")

    monkeypatch.setattr(webui, "_proxy_stream", proxy)
    monkeypatch.setattr(subprocess, "run", controlled_run)

    async def exercise():
        transport = httpx.ASGITransport(app=webui.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            requests = [
                (route, source)
                for source in sources
                for route in ("video", "audio")
            ]
            requests.extend([("video", sources[0])] * 2)

            async def fetch(route, source):
                response = await client.get(f"/{route}", params={"url": source})
                return response, route, source

            release_timer = threading.Timer(0.4, release_resolvers.set)
            release_timer.start()
            try:
                return await asyncio.gather(
                    *(fetch(route, source) for route, source in requests)
                )
            finally:
                release_resolvers.set()
                release_timer.cancel()

    responses = asyncio.run(exercise())

    expected_keys = {
        (source, "bv*" if route == "video" else "ba")
        for source in sources
        for route in ("video", "audio")
    }
    assert calls == Counter({key: 1 for key in expected_keys})
    assert max_active_resolvers == 4
    for response, route, source in responses:
        media_format = "bv*" if route == "video" else "ba"
        assert response.status_code == 200, response.text
        assert response.text == _resolved_url(source, media_format)


@pytest.mark.parametrize("route", ["video", "audio", "shared-media"])
def test_media_routes_identify_platform_resolution_failures(monkeypatch, route):
    source = _page_url(f"failure-{route}") + "?token=private-source-token"
    resolver_calls = []

    def failing_run(args, **_kwargs):
        resolver_calls.append(args)
        return subprocess.CompletedProcess(
            args, 1, stdout="", stderr="fixture failure https://cdn.test/media.m3u8?token=private-source-token"
        )

    monkeypatch.setattr(webui, "_lan_ipv4_candidates", lambda: ["192.168.1.2"])
    monkeypatch.setattr(subprocess, "run", failing_run)

    async def exercise():
        transport = httpx.ASGITransport(
            app=webui.app, raise_app_exceptions=False
        )
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            if route == "shared-media":
                started = await client.post("/share/start", json={"url": source})
                assert started.status_code == 200, started.text
                token = started.json()["token"]
                joined = await client.post(f"/share/{token}/join")
                response = await client.get(joined.json()["video"])
                await client.post(f"/share/stop/{token}")
            else:
                response = await client.get(f"/{route}", params={"url": source})
            return response

    response = asyncio.run(exercise())

    assert len(resolver_calls) == 2
    assert response.status_code == 502
    assert "平台页面解析失败" in response.json()["detail"]
    assert "private-source-token" not in response.text


@pytest.mark.parametrize("route", ["video", "audio"])
def test_media_pull_failure_has_its_own_http_error(monkeypatch, route):
    async def fail_to_pull(_request, url, **_kwargs):
        raise httpx.ConnectError(f"fixture stream failure: {url}")

    monkeypatch.setattr(webui, "_proxy_stream", fail_to_pull)
    source = "https://media.test/watch.mp4?token=private-pull-token"

    async def exercise():
        transport = httpx.ASGITransport(app=webui.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(f"/{route}", params={"url": source})

    response = asyncio.run(exercise())

    assert response.status_code == 502
    assert "媒体拉流失败" in response.json()["detail"]
    assert "private-pull-token" not in response.text


def test_channel_list_read_failure_is_distinct_from_parse_failure(monkeypatch, tmp_path):
    class FailedAsyncClient:
        def __init__(self, *_args, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): pass
        async def get(self, url, **_kwargs):
            request = httpx.Request("GET", url)
            raise httpx.ConnectError("fixture connection failure", request=request)

    monkeypatch.setattr(httpx, "AsyncClient", FailedAsyncClient)
    malformed = tmp_path / "not-a-channel-list.m3u"
    malformed.write_text(
        "#EXTM3U\n#EXT-X-TARGETDURATION:6\nsegment.ts\n", encoding="utf-8"
    )

    with TestClient(webui.app, raise_server_exceptions=False) as client:
        read_response = client.get("/channels", params={
            "url": "https://channels.test/list.m3u?token=private-channel-token",
        })
        parse_response = client.get("/channels", params={"url": str(malformed)})

    assert read_response.status_code == 502
    assert "频道列表读取失败" in read_response.json()["detail"]
    assert "private-channel-token" not in read_response.text
    assert parse_response.status_code == 422
    assert "频道列表解析失败" in parse_response.json()["detail"]
