"""Original-language captions through the public cloud HTTP/WS seam."""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import webui
from cloud_translation import CloudConfigStore


@pytest.mark.parametrize("text,request_data,hints", [
    ("别太装。我们还是很期待的。", {}, []),
    ("Please keep the original English words.", {}, []),
    ("别太装。", {"source": "zh", "target": "en"}, ["zh"]),
    ("Keep these English words.", {"source": "en", "target": "zh"}, ["en"]),
])
def test_default_cloud_session_keeps_original_speech_without_translation(tmp_path, monkeypatch, text, request_data, hints):
    monkeypatch.setattr(webui, "_CLOUD", CloudConfigStore(tmp_path / "cloud.bin"))
    monkeypatch.setattr(webui, "_CLOUD_JOBS", {})
    parameters = []

    class Source:
        def __init__(self, *_args, **_kwargs):
            pass
        def iter_chunks(self):
            yield b"\x00" * 3200
        def close(self):
            pass

    class Engine:
        def transcribe_chunk(self, _audio):
            return (0.0, 1.0, text, None, True, 1)
        def poll(self):
            return (None, None, "", None)
        def finish(self):
            return self.poll()
        def close(self):
            pass

    def create(_name, **kwargs):
        parameters.append(kwargs)
        return Engine()

    def forbidden_translator(*_args, **_kwargs):
        pytest.fail("original-language captions must never invoke a translator")

    monkeypatch.setattr(webui, "StreamSource", Source)
    monkeypatch.setattr(webui, "create_engine", create)
    monkeypatch.setattr(webui, "QwenTranslator", forbidden_translator)
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "test-private-key"})
        ticket = client.post("/api/cloud/sessions", json={"url": "https://media.test/video.mp4", **request_data})
        assert ticket.status_code == 200, ticket.text
        assert ticket.json()["mode"] == "transcribe"
        with client.websocket_connect("/ws?session=" + ticket.json()["session"]) as socket:
            events = []
            while True:
                event = socket.receive_json()
                events.append(event)
                if event["type"] == "end":
                    break
    captions = [event for event in events if event["type"] == "subtitle"]
    assert captions and "".join(cue["text"] for cue in captions) == text
    assert all(cue["is_final"] for cue in captions)
    assert not any(cue.get("translated") for cue in captions)
    assert parameters[0]["language_hints"] == hints
    assert parameters[0]["api_key"] == "test-private-key"


def test_connection_check_does_not_start_a_translation_request(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "_CLOUD", CloudConfigStore(tmp_path / "cloud.bin"))
    calls = []
    class Engine:
        def poll(self):
            return (None, None, "", None)
        def close(self):
            calls.append("closed")
    def create(_name, **kwargs):
        assert kwargs["language_hints"] == []
        calls.append("asr")
        return Engine()
    def forbidden(*_args, **_kwargs):
        pytest.fail("connection check must not call translation")
    monkeypatch.setattr(webui, "create_engine", create)
    monkeypatch.setattr(webui, "QwenTranslator", forbidden)
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "test-private-key"})
        response = client.post("/api/cloud/check")
        assert response.status_code == 200
        assert "已发起连接" in response.json()["message"]
        assert "实际认证和语音识别将在播放时验证" in response.json()["message"]
    assert calls == ["asr", "closed"]


def test_original_language_selection_is_validated_before_any_cloud_request(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "_CLOUD", CloudConfigStore(tmp_path / "cloud.bin"))
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "test-private-key"})
        response = client.post("/api/cloud/sessions", json={"url": "https://media.test/file.mp4",
            "mode": "transcribe", "source": "unsupported"})
        assert response.status_code == 400
        assert client.get("/api/cloud").json()["default_mode"] == "transcribe"


def test_silent_source_releases_playback_without_inventing_a_caption(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, '_CLOUD', CloudConfigStore(tmp_path / 'cloud.bin'))
    monkeypatch.setattr(webui, '_CLOUD_JOBS', {})
    class Source:
        def __init__(self, *_args, **_kwargs): pass
        def iter_chunks(self):
            for _ in range(20): yield b'\x00' * 3200
        def close(self): pass
    class Engine:
        def transcribe_chunk(self, _audio): return self.poll()
        def poll(self): return (None, None, '', None)
        def finish(self): return self.poll()
        def close(self): pass
    monkeypatch.setattr(webui, 'StreamSource', Source)
    monkeypatch.setattr(webui, 'create_engine', lambda *_args, **_kw: Engine())
    monkeypatch.setattr(webui.os.path, 'isfile', lambda _path: True)
    with TestClient(webui.app) as client:
        client.put('/api/cloud', json={'api_key': 'test-private-key'})
        ticket = client.post('/api/cloud/sessions', json={'url':'https://media.test/silent.mp4'}).json()
        with client.websocket_connect('/ws?session=' + ticket['session']) as socket:
            events=[]
            while True:
                event=socket.receive_json(); events.append(event)
                if event['type']=='end': break
    assert any(e['type']=='ready' and e.get('silent') for e in events)
    assert not any(e['type'] in ('subtitle','preview') for e in events)
    assert any(e.get('local_silence_through', 0) and e['local_silence_through'] >= 1.5 for e in events)


def test_undecodable_audio_is_rejected_before_recognizer_creation(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, '_CLOUD', CloudConfigStore(tmp_path / 'cloud.bin'))
    monkeypatch.setattr(webui, '_CLOUD_JOBS', {})
    recognizers = []

    class Source:
        def __init__(self, *_args, **_kwargs): pass
        def iter_chunks(self):
            raise RuntimeError('fixture decoder failure')
            yield b''
        def close(self): pass

    class Engine:
        def close(self): pass

    def create_engine(*_args, **_kwargs):
        recognizers.append('created')
        return Engine()

    monkeypatch.setattr(webui, 'StreamSource', Source)
    monkeypatch.setattr(webui, 'create_engine', create_engine)
    with TestClient(webui.app) as client:
        client.put('/api/cloud', json={'api_key': 'test-private-key'})
        ticket = client.post('/api/cloud/sessions', json={
            'url': 'https://media.test/invalid-audio.mp4',
        }).json()
        with client.websocket_connect('/ws?session=' + ticket['session']) as socket:
            events = []
            while True:
                event = socket.receive_json()
                events.append(event)
                if event['type'] == 'end':
                    break

    error = next(event for event in events if event['type'] == 'error')
    assert recognizers == []
    assert '解码' in error['message']


def test_missing_audio_track_is_actionable_and_never_starts_recognition(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, '_CLOUD', CloudConfigStore(tmp_path / 'cloud.bin'))
    monkeypatch.setattr(webui, '_CLOUD_JOBS', {})
    recognizers = []
    signed_url = 'https://media.test/no-audio.mp4?token=private-no-audio-token'

    class Source:
        def __init__(self, url, **_kwargs):
            raise ValueError(f'输入中没有音频流: {url}')

    monkeypatch.setattr(webui, 'StreamSource', Source)
    monkeypatch.setattr(webui, 'create_engine', lambda *_args, **_kwargs: recognizers.append('created'))
    with TestClient(webui.app) as client:
        client.put('/api/cloud', json={'api_key': 'test-private-key'})
        ticket = client.post('/api/cloud/sessions', json={'url': signed_url}).json()
        with client.websocket_connect('/ws?session=' + ticket['session']) as socket:
            events = []
            while True:
                event = socket.receive_json()
                events.append(event)
                if event['type'] == 'end':
                    break

    error = next(event for event in events if event['type'] == 'error')
    assert recognizers == []
    assert '没有音频轨' in error['message']
    assert 'private-no-audio-token' not in str(events)
    assert 'test-private-key' not in str(events)


def test_known_unplayable_mpeg2_input_is_rejected_before_recognition(tmp_path, monkeypatch):
    import types
    monkeypatch.setattr(webui, '_CLOUD', CloudConfigStore(tmp_path / 'cloud.bin'))
    monkeypatch.setattr(webui, '_CLOUD_JOBS', {})
    recognizers = []

    class Source:
        def __init__(self, *_args, **_kwargs):
            self.container = types.SimpleNamespace(streams=types.SimpleNamespace(video=[
                types.SimpleNamespace(codec_context=types.SimpleNamespace(name='mpeg2video'))
            ]))
        def iter_chunks(self):
            raise AssertionError('known unsupported video must fail before audio decoding')
            yield b''
        def close(self): pass

    monkeypatch.setattr(webui, 'StreamSource', Source)
    monkeypatch.setattr(webui, 'create_engine', lambda *_args, **_kwargs: recognizers.append('created'))
    with TestClient(webui.app) as client:
        client.put('/api/cloud', json={'api_key': 'test-private-key'})
        ticket = client.post('/api/cloud/sessions', json={
            'url': 'https://media.test/known-mpeg2.mp4',
        }).json()
        with client.websocket_connect('/ws?session=' + ticket['session']) as socket:
            events = []
            while True:
                event = socket.receive_json()
                events.append(event)
                if event['type'] == 'end':
                    break

    error = next(event for event in events if event['type'] == 'error')
    assert recognizers == []
    assert 'MPEG-2' in error['message']
    assert '无法播放' in error['message']


def test_cloud_session_diagnostics_hide_signed_media_url_parameters(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, '_CLOUD', CloudConfigStore(tmp_path / 'cloud.bin'))
    monkeypatch.setattr(webui, '_CLOUD_JOBS', {})

    class Source:
        def __init__(self, *_args, **_kwargs): pass
        def iter_chunks(self):
            yield b'\x00' * 3200
        def close(self): pass

    class Engine:
        def transcribe_chunk(self, _audio): return (None, None, '', None)
        def poll(self): return (None, None, '', None)
        def finish(self): return self.poll()
        def close(self): pass

    monkeypatch.setattr(webui, 'StreamSource', Source)
    monkeypatch.setattr(webui, 'create_engine', lambda *_args, **_kwargs: Engine())
    signed_url = 'https://media.test/live.m3u8?token=private-media-token&sig=private-signature'
    with TestClient(webui.app) as client:
        config_response = client.put('/api/cloud', json={'api_key': 'test-private-key'})
        assert 'test-private-key' not in config_response.text
        ticket = client.post('/api/cloud/sessions', json={'url': signed_url})
        assert ticket.status_code == 200
        assert 'private-media-token' not in ticket.text
        with client.websocket_connect('/ws?session=' + ticket.json()['session']) as socket:
            events = []
            while True:
                event = socket.receive_json()
                events.append(event)
                if event['type'] == 'end':
                    break

    assert any(event['type'] == 'status' and '正在检查直播源:' in event['message']
               for event in events)
    assert 'private-media-token' not in str(events)
    assert 'private-signature' not in str(events)
    assert 'test-private-key' not in str(events)


def test_local_audio_is_bounded_ahead_of_the_realtime_recognizer(tmp_path, monkeypatch):
    import time
    monkeypatch.setattr(webui, '_CLOUD', CloudConfigStore(tmp_path / 'cloud.bin'))
    monkeypatch.setattr(webui, '_CLOUD_JOBS', {})
    sent=[]
    class Source:
        def __init__(self, *_args, **_kwargs): pass
        def iter_chunks(self):
            for _ in range(30): yield b'\x00' * 3200
        def close(self): pass
    class Engine:
        def transcribe_chunk(self, _audio): sent.append(time.monotonic()); return self.poll()
        def poll(self): return (None,None,'',None)
        def finish(self): return self.poll()
        def close(self): pass
    monkeypatch.setattr(webui, 'StreamSource', Source)
    monkeypatch.setattr(webui, 'create_engine', lambda *_args, **_kwargs: Engine())
    monkeypatch.setattr(webui.os.path, 'isfile', lambda _p: True)
    with TestClient(webui.app) as client:
        client.put('/api/cloud',json={'api_key':'test-private-key'})
        ticket=client.post('/api/cloud/sessions',json={'url':'https://media.test/bounded.mp4'}).json()
        with client.websocket_connect('/ws?session='+ticket['session']) as socket:
            while socket.receive_json()['type']!='end': pass
    assert len(sent)==30
    assert sent[-1]-sent[0] >= .85, 'three seconds of audio must not be sent in one burst'
