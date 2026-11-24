"""#115: unparseable proxy environment must not disable probing and channel reads."""
import asyncio

import httpx
import pytest

from lisca.web import app as webui


def _close(client):
    asyncio.run(client.aclose())


def test_http_client_falls_back_when_no_proxy_cannot_be_parsed(monkeypatch):
    for key in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(key, "localhost,[::1]")
    with pytest.raises(httpx.InvalidURL):
        httpx.AsyncClient()

    client = webui._http_client()
    try:
        assert client.trust_env is False
    finally:
        _close(client)


def test_http_client_keeps_environment_proxies_when_they_parse(monkeypatch):
    for key in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(key, "localhost,127.0.0.1,::1")
    client = webui._http_client()
    try:
        assert client.trust_env is True
    finally:
        _close(client)
