"""#111: local operation records, session filtering, redaction and retention."""

import json
import os
import time
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from lisca.web import app as webui
from lisca.web.operation_log import OpsLog


def test_append_read_and_session_filter(tmp_path):
    ops = OpsLog(tmp_path)
    ops.append({"kind": "user", "session": "a", "action": "scan_start", "detail": "11473 个频道"})
    ops.append({"kind": "http", "session": "b", "method": "GET", "path": "/probe",
                "status": 200, "dur_ms": 3})

    assert {event["action"] for event in ops.read(session="a")} == {"scan_start"}
    assert [event["path"] for event in ops.read(session="b")] == ["/probe"]
    assert len(ops.read()) == 2
    assert json.loads(ops.export(session="a").strip())["detail"] == "11473 个频道"


def test_export_redacts_urls_unless_full_is_requested(tmp_path):
    ops = OpsLog(tmp_path)
    ops.append({"kind": "user", "session": "s", "action": "channels_load",
                "detail": "https://media.test/list.m3u?token=private-token"})

    assert "private-token" not in ops.export(session="s")
    assert "media.test/list.m3u" in ops.export(session="s")
    assert "private-token" in ops.export(session="s", full=True)


def test_prune_removes_only_old_or_oversized_ops_files(tmp_path):
    ops = OpsLog(tmp_path, max_age_days=7, max_bytes=200)
    old = tmp_path / "ops-20200101.jsonl"
    old.write_text('{"kind":"user"}\n', encoding="utf-8")
    stamp = (datetime.now() - timedelta(days=30)).timestamp()
    os.utime(old, (stamp, stamp))
    unrelated = tmp_path / "app.log"
    unrelated.write_text("keep me", encoding="utf-8")
    ops.append({"kind": "user", "session": "s", "action": "x"})

    ops.prune()

    assert not old.exists(), "files older than the retention window are removed"
    assert unrelated.exists(), "non-operation logs are untouched"
    total = sum(path.stat().st_size for path in ops.files())
    assert total <= 200, "the newest records stay within the byte budget"


def test_ops_route_records_user_actions_and_http_requests(tmp_path, monkeypatch):
    ops = OpsLog(tmp_path)
    monkeypatch.setattr(webui, "_OPS", ops)

    with TestClient(webui.app, raise_server_exceptions=False) as client:
        posted = client.post(
            "/ops",
            headers={"X-Lisca-Session-Id": "session-1", "X-Lisca-Request-Id": "req-1"},
            json={"events": [{"action": "scan_start", "detail": "11473 个频道",
                              "req_id": "req-1"}]})
        missing = client.get("/channels", params={"url": str(tmp_path / "missing.m3u")},
                             headers={"X-Lisca-Session-Id": "session-1"})
        listed = client.get("/api/ops", params={"session": "session-1"})

    assert posted.json() == {"recorded": 1}
    assert missing.status_code == 502
    events = listed.json()["events"]
    user = [event for event in events if event["kind"] == "user"]
    http = [event for event in events if event["kind"] == "http"]
    assert user and user[0]["action"] == "scan_start"
    channel_http = [event for event in http if event["path"] == "/channels"]
    assert channel_http and channel_http[0]["status"] == 502
    assert user[0]["session"] == "session-1"


def test_export_route_defaults_to_redacted_jsonl(tmp_path, monkeypatch):
    ops = OpsLog(tmp_path)
    ops.append({"kind": "user", "session": "s", "action": "channels_load",
                "detail": "https://media.test/list.m3u?token=private-token"})
    monkeypatch.setattr(webui, "_OPS", ops)

    with TestClient(webui.app) as client:
        default = client.get("/api/ops/export", params={"session": "s"})
        full = client.get("/api/ops/export", params={"session": "s", "full": 1})

    assert default.headers["content-type"].startswith("application/x-ndjson")
    assert "private-token" not in default.text
    assert "private-token" in full.text


def test_probe_requests_are_aggregated_instead_of_recorded_individually():
    assert webui._recordable_request("/channels")
    assert not webui._recordable_request("/probe")
    assert not webui._recordable_request("/api/ops")
