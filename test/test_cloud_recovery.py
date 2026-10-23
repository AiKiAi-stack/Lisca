"""Recovery behavior through the owner HTTP session and WebSocket interfaces."""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import webui  # noqa: E402
from cloud_translation import CloudConfigStore  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "_CLOUD", CloudConfigStore(tmp_path / "cloud.bin"))
    monkeypatch.setattr(webui, "_CLOUD_JOBS", {})


class StructuredProviderFailure(Exception):
    def __init__(self, status_code, code):
        super().__init__("sensitive provider detail")
        self.status_code = status_code
        self.code = code


def test_cloud_ws_exposes_provider_permission_evidence_without_raw_error(monkeypatch):
    class Source:
        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            yield b"\x00" * 3200

        def close(self):
            pass

    class Engine:
        def transcribe_chunk(self, _audio):
            raise StructuredProviderFailure(403, "Model.AccessDenied")

        def close(self):
            pass

    monkeypatch.setattr(webui, "StreamSource", Source)
    monkeypatch.setattr(webui, "create_engine", lambda *_args, **_kwargs: Engine())
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "TEST_ONLY_KEY"})
        ticket = client.post("/api/cloud/sessions", json={
            "mode": "transcribe", "url": "https://media.test/clip.mp4",
            "source": "auto",
        }).json()
        with client.websocket_connect("/ws?session=" + ticket["session"]) as ws:
            events = []
            while True:
                event = ws.receive_json()
                events.append(event)
                if event["type"] == "end":
                    break

    error = next(event for event in events if event["type"] == "error")
    assert error["failure_category"] == "permission"
    assert error["provider_status"] == 403
    assert error["provider_code"] == "Model.AccessDenied"
    assert "sensitive provider detail" not in str(events)
    assert "TEST_ONLY_KEY" not in str(events)
    assert events[-1] == {"type": "end", "reason": "error"}


@pytest.mark.parametrize(("failure", "category", "retryable", "code"), [
    (StructuredProviderFailure(401, "InvalidApiKey"), "authentication", False, "InvalidApiKey"),
    (StructuredProviderFailure(429, "BudgetLimitExceeded"), "quota", False,
     "BudgetLimitExceeded"),
    (StructuredProviderFailure(429, "Throttling.RateQuota"), "rate_limited", True,
     "Throttling.RateQuota"),
    (ConnectionError("private network detail"), "network", True, None),
    (StructuredProviderFailure(503, "UnknownServiceCode"), "temporarily_unavailable", True, None),
    (StructuredProviderFailure(400, "UnknownPrivateCode"), "unknown", False, None),
])
def test_cloud_ws_classifies_failures_only_from_provider_evidence(
        monkeypatch, failure, category, retryable, code):
    class Source:
        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            yield b"\x00" * 3200

        def close(self):
            pass

    class Engine:
        def transcribe_chunk(self, _audio):
            raise failure

        def close(self):
            pass

    monkeypatch.setattr(webui, "StreamSource", Source)
    monkeypatch.setattr(webui, "create_engine", lambda *_args, **_kwargs: Engine())
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "TEST_ONLY_KEY"})
        ticket = client.post("/api/cloud/sessions", json={
            "mode": "transcribe", "url": "https://media.test/clip.mp4",
            "source": "auto",
        }).json()
        with client.websocket_connect("/ws?session=" + ticket["session"]) as ws:
            events = []
            while True:
                event = ws.receive_json()
                events.append(event)
                if event["type"] == "end":
                    break

    error = next(event for event in events if event["type"] == "error")
    assert error["failure_category"] == category
    assert error["retryable"] is retryable
    assert error["provider_code"] == code
    assert "private" not in str(events)
    assert "TEST_ONLY_KEY" not in str(events)
