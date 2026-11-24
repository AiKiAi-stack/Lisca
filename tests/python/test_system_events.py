"""#114: backend action events, persistent logs and the agent-facing reader."""
import json
import logging

from fastapi.testclient import TestClient

from lisca.web import app as webui
from lisca.web.operation_log import OpsLog
from lisca import ops as ops_cli


def _client():
    return TestClient(webui.app, raise_server_exceptions=False)


def test_channel_load_and_failure_are_recorded_with_the_session(tmp_path, monkeypatch):
    store = OpsLog(tmp_path)
    monkeypatch.setattr(webui, "_OPS", store)
    playlist = tmp_path / "list.m3u"
    playlist.write_text(
        "#EXTM3U\n#EXTINF:-1 group-title=\"新闻\",频道一\nhttp://media.test/a.m3u8\n",
        encoding="utf-8")

    with _client() as client:
        ok = client.get("/channels", params={"url": str(playlist)},
                        headers={"X-Lisca-Session-Id": "session-1"})
        failed = client.get("/channels", params={"url": str(tmp_path / "missing.m3u")},
                            headers={"X-Lisca-Session-Id": "session-1"})

    assert ok.status_code == 200 and len(ok.json()) == 1
    assert failed.status_code == 502
    events = store.read(session="session-1")
    loaded = [event for event in events if event.get("action") == "channels_loaded"]
    failed_events = [event for event in events if event.get("action") == "channels_failed"]
    assert loaded and loaded[0]["count"] == 1 and loaded[0]["source"] == "file"
    assert "dur_ms" in loaded[0]
    assert failed_events and failed_events[0]["reason"] == "read"


def test_upload_share_and_cloud_actions_are_recorded(tmp_path, monkeypatch):
    store = OpsLog(tmp_path)
    monkeypatch.setattr(webui, "_OPS", store)

    with _client() as client:
        uploaded = client.post("/upload", files={"file": ("clip.mp4", b"0123456789")},
                               headers={"X-Lisca-Session-Id": "session-2"})
        cloud = client.delete("/api/cloud",
                              headers={"X-Lisca-Session-Id": "session-2"})

    assert uploaded.status_code == 200
    assert cloud.status_code == 200
    events = store.read(session="session-2")
    upload = [event for event in events if event.get("action") == "upload"]
    config = [event for event in events if event.get("action") == "cloud_config"]
    assert upload and upload[0]["name"] == "clip.mp4" and upload[0]["bytes"] == 10
    assert config and config[0]["saved"] is False


def test_file_logging_persists_backend_detail(tmp_path):
    handler = webui._install_file_logging(tmp_path)
    try:
        logging.getLogger("lisca.test").warning("detail-%s", "probe failed")
        handler.flush()
        files = list(tmp_path.glob("webui-*.log"))
        assert files, "the detailed backend log file is created"
        assert "detail-probe failed" in files[0].read_text(encoding="utf-8")
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()


def test_ops_cli_reads_and_redacts_for_agents(tmp_path, capsys):
    store = OpsLog(tmp_path)
    store.append({"kind": "user", "session": "s", "action": "channels_load",
                  "detail": "https://media.test/list.m3u?token=private-token"})
    store.append({"kind": "system", "session": "s", "action": "channels_loaded",
                  "count": 60, "dur_ms": 12.5})

    assert ops_cli.main(["--session", "s", "--dir", str(tmp_path)]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2
    assert "private-token" not in lines[0]
    assert json.loads(lines[1])["count"] == 60

    assert ops_cli.main(["--kind", "system", "--dir", str(tmp_path)]) == 0
    only_system = capsys.readouterr().out.strip().splitlines()
    assert len(only_system) == 1

    assert ops_cli.main(["--session", "s", "--full", "--dir", str(tmp_path)]) == 0
    assert "private-token" in capsys.readouterr().out
