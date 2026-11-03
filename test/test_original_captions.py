"""Original-language captions through the public cloud HTTP/WS seam."""
import os
import sys
import threading

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import webui
from cloud_translation import CloudConfigStore
from stream_source import AudioTimelineError


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
    assert events[-1] == {"type": "end", "reason": "completed"}
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


def test_deleting_cloud_session_ends_recording_as_cancelled(tmp_path, monkeypatch):
    import threading

    monkeypatch.setattr(webui, "_CLOUD", CloudConfigStore(tmp_path / "cloud.bin"))
    monkeypatch.setattr(webui, "_CLOUD_JOBS", {})
    recognizer_started = threading.Event()

    class Source:
        def __init__(self, *_args, **_kwargs): pass
        def iter_chunks(self):
            while True:
                yield b"\x00" * 3200
        def close(self): pass

    class Engine:
        def transcribe_chunk(self, _audio):
            recognizer_started.set()
            return (0.0, 0.1, "partial", None, True, 1)
        def poll(self): return (None, None, "", None)
        def finish(self): return self.poll()
        def close(self): pass

    monkeypatch.setattr(webui, "StreamSource", Source)
    monkeypatch.setattr(webui, "create_engine", lambda *_args, **_kwargs: Engine())
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "test-private-key"})
        ticket = client.post("/api/cloud/sessions", json={
            "url": "https://media.test/live.m3u8",
        }).json()
        with client.websocket_connect("/ws?session=" + ticket["session"]) as socket:
            assert recognizer_started.wait(2)
            assert client.delete("/api/cloud/sessions/" + ticket["session"]).json() == {"ok": True}
            events = []
            while True:
                event = socket.receive_json()
                events.append(event)
                if event["type"] == "end":
                    break

    assert events[-1] == {"type": "end", "reason": "cancelled"}


def test_deleting_local_session_interrupts_realtime_feed_pacing(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, '_CLOUD', CloudConfigStore(tmp_path / 'cloud.bin'))
    monkeypatch.setattr(webui, '_CLOUD_JOBS', {})
    monkeypatch.setattr(webui.os.path, 'isfile', lambda _path: True)
    media = tmp_path / 'paced-local.mp4'
    media.touch()
    pacing_wait_started = threading.Event()
    sent = []
    audio = b'\x00\x10' * 1600

    class Source:
        def __init__(self, *_args, **_kwargs): pass
        def iter_chunks(self):
            for _ in range(100):
                yield audio
        def close(self): pass

    class Engine:
        def transcribe_chunk(self, _audio):
            sent.append(len(sent))
            return (None, None, '', None)
        def poll(self): return (None, None, '', None)
        def finish(self): return self.poll()
        def close(self): pass

    def block_until_stopped(stop, seconds):
        assert seconds > 0
        pacing_wait_started.set()
        return stop.wait(10)

    monkeypatch.setattr(webui, '_session_wait', block_until_stopped)
    monkeypatch.setattr(webui, 'StreamSource', Source)
    monkeypatch.setattr(webui, 'create_engine', lambda *_args, **_kwargs: Engine())
    with TestClient(webui.app) as client:
        client.put('/api/cloud', json={'api_key': 'test-private-key'})
        ticket = client.post('/api/cloud/sessions', json={
            'url': str(media),
        }).json()
        with client.websocket_connect('/ws?session=' + ticket['session']) as socket:
            assert pacing_wait_started.wait(2)
            stopped_at = len(sent)
            assert client.delete('/api/cloud/sessions/' + ticket['session']).json() == {'ok': True}
            events = []
            while True:
                event = socket.receive_json()
                events.append(event)
                if event['type'] == 'end':
                    break

    assert len(sent) == stopped_at < 100
    assert events[-1] == {'type': 'end', 'reason': 'cancelled'}


def test_recognition_error_ends_cloud_session_as_error(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "_CLOUD", CloudConfigStore(tmp_path / "cloud.bin"))
    monkeypatch.setattr(webui, "_CLOUD_JOBS", {})

    class Source:
        def __init__(self, *_args, **_kwargs): pass
        def iter_chunks(self):
            yield b"\x00" * 3200
        def close(self): pass

    class Engine:
        def transcribe_chunk(self, _audio):
            raise RuntimeError("test recognizer failure")
        def poll(self): return (None, None, "", None)
        def finish(self): return self.poll()
        def close(self): pass

    monkeypatch.setattr(webui, "StreamSource", Source)
    monkeypatch.setattr(webui, "create_engine", lambda *_args, **_kwargs: Engine())
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "test-private-key"})
        ticket = client.post("/api/cloud/sessions", json={
            "url": "https://media.test/error.mp4",
        }).json()
        with client.websocket_connect("/ws?session=" + ticket["session"]) as socket:
            events = []
            while True:
                event = socket.receive_json()
                events.append(event)
                if event["type"] == "end":
                    break

    assert any(event["type"] == "error" for event in events)
    assert events[-1] == {"type": "end", "reason": "error"}


def test_original_caption_revisions_increase_across_withdrawal_and_correction(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "_CLOUD", CloudConfigStore(tmp_path / "cloud.bin"))
    monkeypatch.setattr(webui, "_CLOUD_JOBS", {})

    class Source:
        def __init__(self, *_args, **_kwargs): pass
        def iter_chunks(self):
            for _ in range(8):
                yield b"\x00" * 3200
        def close(self): pass

    class Engine:
        def __init__(self):
            self.events = iter([
                (0.0, 1.0, "先前原文。", None, True, 7),
                (0.0, 1.0, "撤回前修订。", None, True, 7),
                (0.0, 1.0, "当前草稿也要撤回。", None, False, 7),
                (0.0, 1.0, "", None, True, 7),
                (0.0, 1.0, "先前原文。", None, True, 7),
                (0.0, 1.0, "撤回前修订。", None, True, 7),
                (0.0, 1.0, "当前草稿也要撤回。", None, False, 7),
                (0.0, 1.0, "修订后的原文。", None, True, 7),
            ])
        def transcribe_chunk(self, _audio):
            return next(self.events, (None, None, "", None))
        def poll(self): return (None, None, "", None)
        def finish(self): return self.poll()
        def close(self): pass

    monkeypatch.setattr(webui, "StreamSource", Source)
    monkeypatch.setattr(webui, "create_engine", lambda *_args, **_kwargs: Engine())
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "test-private-key"})
        ticket = client.post("/api/cloud/sessions", json={
            "url": "https://media.test/revision.mp4",
        }).json()
        with client.websocket_connect("/ws?session=" + ticket["session"]) as socket:
            events = []
            while True:
                event = socket.receive_json()
                events.append(event)
                if event["type"] == "end":
                    break

    old = next(event for event in events
               if event["type"] == "subtitle" and event["text"] == "先前原文。")
    removed = next(event for event in events if event["type"] == "caption_remove")
    corrected = next(event for event in events
                     if event["type"] == "subtitle" and event["text"] == "修订后的原文。")
    withdrawn_draft = next(event for event in events
                           if event["type"] == "subtitle" and event["text"] == "当前草稿也要撤回。")
    captions = [event for event in events if event["type"] == "subtitle"]
    assert [event["text"] for event in captions] == [
        "先前原文。", "撤回前修订。", "当前草稿也要撤回。", "修订后的原文。"]
    assert old["sentence_id"] == removed["sentence_id"] == corrected["sentence_id"] == 7
    assert old["revision"] < removed["revision"] < corrected["revision"]
    assert withdrawn_draft["revision"] < removed["revision"]
    assert corrected["revision"] == removed["revision"] + 1


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
    assert any(e.get('local_silence_from') == 0 and e.get('local_silence_through', 0) >= 1.5 for e in events)


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


def test_live_hls_buffer_is_not_throttled_again_after_segment_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, '_CLOUD', CloudConfigStore(tmp_path / 'cloud.bin'))
    monkeypatch.setattr(webui, '_CLOUD_JOBS', {})
    clock = [0.0]
    sent_at = []
    audio = b'\x00\x10' * 1600  # 100ms at 16 kHz mono int16.

    class Source:
        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            for index in range(30):
                if index in (10, 20):
                    clock[0] += 2.0  # A delayed segment read; ten PCM chunks follow it.
                yield audio

        def close(self):
            pass

    class Engine:
        def transcribe_chunk(self, _audio):
            sent_at.append(clock[0])
            return (None, None, '', None)

        def poll(self):
            return (None, None, '', None)

        def finish(self):
            return self.poll()

        def close(self):
            pass

    def advance_clock(stop, seconds):
        clock[0] += seconds
        return stop.is_set()

    monkeypatch.setattr(webui, '_session_monotonic', lambda: clock[0])
    monkeypatch.setattr(webui, '_session_wait', advance_clock)
    monkeypatch.setattr(webui, 'StreamSource', Source)
    monkeypatch.setattr(webui, 'create_engine', lambda *_args, **_kwargs: Engine())

    with TestClient(webui.app) as client:
        client.put('/api/cloud', json={'api_key': 'test-private-key'})
        ticket = client.post('/api/cloud/sessions', json={
            'url': 'https://media.test/live.m3u8',
        }).json()
        with client.websocket_connect('/ws?session=' + ticket['session']) as socket:
            while socket.receive_json()['type'] != 'end':
                pass

    assert len(sent_at) == 30
    assert sent_at[:10] == pytest.approx([0.0] * 10), "buffered live audio must reach ASR before playback overtakes it"
    assert sent_at[10:20] == pytest.approx([2.0] * 10)
    assert sent_at[20:] == pytest.approx([4.0] * 10)


def test_live_pacing_excludes_user_pause_and_stop_drops_unsent_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, '_CLOUD', CloudConfigStore(tmp_path / 'cloud.bin'))
    monkeypatch.setattr(webui, '_CLOUD_JOBS', {})
    clock = [0.0]
    worker_ident = []
    pause_command_sent = threading.Event()
    pause_observed = threading.Event()
    resume_command_sent = threading.Event()
    resume_observed = threading.Event()
    waiting_for_second = threading.Event()
    release_second = threading.Event()
    pause_wait_started = threading.Event()
    release_pause_wait = threading.Event()
    waiting_for_third = threading.Event()
    release_third = threading.Event()
    sent_at = []
    audio = b'\x00\x10' * 1600

    class Source:
        def __init__(self, *_args, **_kwargs):
            worker_ident.append(threading.get_ident())

        def iter_chunks(self):
            yield audio
            waiting_for_second.set()
            release_second.wait(3)
            yield audio
            waiting_for_third.set()
            release_third.wait(3)
            yield audio

        def close(self):
            pass

    class Engine:
        def transcribe_chunk(self, _audio):
            sent_at.append(clock[0])
            return (None, None, '', None)

        def poll(self):
            return (None, None, '', None)

        def finish(self):
            return self.poll()

        def close(self):
            pass

    def session_clock():
        if worker_ident and threading.get_ident() != worker_ident[0]:
            if pause_command_sent.is_set():
                pause_observed.set()
            if resume_command_sent.is_set():
                resume_observed.set()
        return clock[0]

    def advance_clock_or_hold_pause(stop, seconds):
        if seconds >= 0.19:
            pause_wait_started.set()
            release_pause_wait.wait(2)
            return stop.is_set()
        clock[0] += seconds
        return stop.is_set()

    monkeypatch.setattr(webui, '_session_monotonic', session_clock)
    monkeypatch.setattr(webui, '_session_wait', advance_clock_or_hold_pause)
    monkeypatch.setattr(webui, 'StreamSource', Source)
    monkeypatch.setattr(webui, 'create_engine', lambda *_args, **_kwargs: Engine())

    try:
        with TestClient(webui.app) as client:
            client.put('/api/cloud', json={'api_key': 'test-private-key'})
            ticket = client.post('/api/cloud/sessions', json={
                'url': 'https://media.test/live.m3u8',
            }).json()
            with client.websocket_connect('/ws?session=' + ticket['session']) as socket:
                assert waiting_for_second.wait(2)
                pause_command_sent.set()
                socket.send_text('pause')
                assert pause_observed.wait(2)
                clock[0] += 10.0
                release_second.set()
                assert pause_wait_started.wait(2)
                resume_command_sent.set()
                socket.send_text('resume')
                assert resume_observed.wait(2)
                release_pause_wait.set()
                assert waiting_for_third.wait(2)

                clock[0] += 2.0  # The source has more audio than the user will accept.
                assert client.delete('/api/cloud/sessions/' + ticket['session']).json() == {'ok': True}
                release_third.set()
                events = []
                while True:
                    event = socket.receive_json()
                    events.append(event)
                    if event['type'] == 'end':
                        break
    finally:
        release_second.set()
        release_pause_wait.set()
        release_third.set()

    assert len(sent_at) == 2
    assert sent_at[1] - sent_at[0] == pytest.approx(10.0)
    assert events[-1] == {'type': 'end', 'reason': 'cancelled'}


def test_thirty_minute_local_silence_prepares_incrementally_with_bounded_feed_and_single_chunk_backlog(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, '_CLOUD', CloudConfigStore(tmp_path / 'cloud.bin'))
    monkeypatch.setattr(webui, '_CLOUD_JOBS', {})
    monkeypatch.setattr(webui.os.path, 'isfile', lambda _path: True)
    clock = [0.0]
    generated = [0]
    sent = []
    fed = [0.0]
    peak_unprocessed_chunks = [0]
    max_audio_lead = [0.0]
    first_pacing_wait = threading.Event()
    allow_pacing = threading.Event()
    audio = b'\x00' * (16000 * 2)  # One second at the 16 kHz mono worker boundary.

    class Source:
        def __init__(self, *_args, **_kwargs): pass
        def iter_chunks(self):
            for _ in range(30 * 60):
                generated[0] += 1
                yield audio
        def close(self): pass

    class Engine:
        def transcribe_chunk(self, pcm):
            peak_unprocessed_chunks[0] = max(
                peak_unprocessed_chunks[0], generated[0] - len(sent))
            fed[0] += len(pcm) / 16000
            sent.append(fed[0])
            max_audio_lead[0] = max(max_audio_lead[0], fed[0] - clock[0])
            return self.poll()
        def poll(self): return (None, None, '', None)
        def finish(self): return self.poll()
        def close(self): pass

    monkeypatch.setattr(webui, '_session_monotonic', lambda: clock[0])
    def fast_forward(stop, seconds):
        if not first_pacing_wait.is_set():
            first_pacing_wait.set()
            if not allow_pacing.wait(3):
                return stop.is_set()
        clock[0] += seconds
        return stop.is_set()
    monkeypatch.setattr(webui, '_session_wait', fast_forward)
    monkeypatch.setattr(webui, 'StreamSource', Source)
    monkeypatch.setattr(webui, 'create_engine', lambda *_args, **_kwargs: Engine())

    with TestClient(webui.app) as client:
        client.put('/api/cloud', json={'api_key': 'test-private-key'})
        ticket = client.post('/api/cloud/sessions', json={'url': 'https://media.test/long-silent.mp4'}).json()
        with client.websocket_connect('/ws?session=' + ticket['session']) as socket:
            events = []
            ready_before_eof = False
            while True:
                event = socket.receive_json()
                events.append(event)
                if event['type'] == 'ready':
                    ready_before_eof = generated[0] < 30 * 60 and len(sent) < 30 * 60
                    allow_pacing.set()
                if event['type'] == 'end':
                    break

    assert generated[0] == len(sent) == 30 * 60
    assert peak_unprocessed_chunks[0] <= 1
    assert max_audio_lead[0] <= 2.0 + 1e-6
    assert ready_before_eof
    assert any(event['type'] == 'ready' and event.get('silent') for event in events)
    assert not any(event['type'] in ('subtitle', 'preview') for event in events)
    assert events[-1] == {'type': 'end', 'reason': 'completed'}


def test_server_stops_local_session_after_fifteen_seconds_without_recognition_progress(tmp_path, monkeypatch):
    import time

    monkeypatch.setattr(webui, '_CLOUD', CloudConfigStore(tmp_path / 'cloud.bin'))
    monkeypatch.setattr(webui, '_CLOUD_JOBS', {})
    media = tmp_path / 'no-progress.wav'
    media.touch()
    sent = []
    closed = []
    audio = b'\x00\x10' * 1600

    class Source:
        def __init__(self, *_args, **_kwargs): pass
        def iter_chunks(self):
            for _ in range(180):
                yield audio
        def close(self): pass

    class Engine:
        def transcribe_chunk(self, _audio):
            sent.append(len(sent))
            return (None, None, '', None)
        def poll(self): return (None, None, '', None)
        def finish(self): return self.poll()
        def close(self): closed.append('closed')

    monkeypatch.setattr(webui, 'StreamSource', Source)
    monkeypatch.setattr(webui, 'create_engine', lambda *_args, **_kwargs: Engine())
    with TestClient(webui.app) as client:
        client.put('/api/cloud', json={'api_key': 'test-private-key'})
        ticket = client.post('/api/cloud/sessions', json={'url': str(media)}).json()
        began = time.monotonic()
        with client.websocket_connect('/ws?session=' + ticket['session']) as socket:
            events = []
            while True:
                event = socket.receive_json()
                events.append(event)
                if event['type'] == 'end':
                    break

    elapsed = time.monotonic() - began
    error = next((event for event in events if event['type'] == 'error'), None)
    assert error is not None, events[-5:]
    assert '15 秒' in error['message']
    assert events[-1]['reason'] == 'error'
    assert 14 <= elapsed < 20, elapsed
    assert 0 < len(sent) < 180, len(sent)
    assert closed == ['closed']


def test_user_pause_is_excluded_from_server_progress_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, '_CLOUD', CloudConfigStore(tmp_path / 'cloud.bin'))
    monkeypatch.setattr(webui, '_CLOUD_JOBS', {})
    monkeypatch.setattr(webui.os.path, 'isfile', lambda _path: True)
    clock = [0.0]
    pause_requested = threading.Event()
    inside_pause = threading.Event()
    first_started = threading.Event()
    release_first = threading.Event()
    second_sent = threading.Event()
    third_sent = threading.Event()
    waiting_third = threading.Event()
    allow_third = threading.Event()
    waiting_fourth = threading.Event()
    allow_fourth = threading.Event()
    sent = []
    audio = b'\x00\x10' * 1600

    class Source:
        def __init__(self, *_args, **_kwargs): pass
        def iter_chunks(self):
            yield audio
            yield audio
            waiting_third.set()
            allow_third.wait(3)
            yield audio
            waiting_fourth.set()
            allow_fourth.wait(3)
            yield audio
        def close(self): pass

    class Engine:
        def transcribe_chunk(self, _audio):
            sent.append(len(sent) + 1)
            if len(sent) == 1:
                first_started.set()
                release_first.wait(3)
            elif len(sent) == 2:
                second_sent.set()
            elif len(sent) == 3:
                third_sent.set()
            return (None, None, '', None)
        def poll(self): return (None, None, '', None)
        def finish(self): return self.poll()
        def close(self): pass

    monkeypatch.setattr(webui, '_session_monotonic', lambda: clock[0])
    def controlled_wait(stop, _seconds):
        if pause_requested.is_set():
            inside_pause.set()
        return stop.wait(0.01)
    monkeypatch.setattr(webui, '_session_wait', controlled_wait)
    monkeypatch.setattr(webui, 'StreamSource', Source)
    monkeypatch.setattr(webui, 'create_engine', lambda *_args, **_kwargs: Engine())

    try:
        with TestClient(webui.app) as client:
            client.put('/api/cloud', json={'api_key': 'test-private-key'})
            ticket = client.post('/api/cloud/sessions', json={'url': 'https://media.test/paused.mp4'}).json()
            with client.websocket_connect('/ws?session=' + ticket['session']) as socket:
                assert first_started.wait(2)
                pause_requested.set()
                socket.send_text('pause')
                release_first.set()
                assert inside_pause.wait(2)
                clock[0] = 20.0
                pause_requested.clear()
                socket.send_text('resume')
                assert second_sent.wait(2)
                assert waiting_third.wait(2)
                clock[0] = 34.0
                allow_third.set()
                assert third_sent.wait(2)
                assert waiting_fourth.wait(2)
                clock[0] = 35.1
                allow_fourth.set()
                events = []
                while True:
                    event = socket.receive_json()
                    events.append(event)
                    if event['type'] == 'end':
                        break
    finally:
        release_first.set()
        allow_third.set()
        allow_fourth.set()

    assert sent == [1, 2, 3]
    assert any(event['type'] == 'error' and '15 秒' in event['message'] for event in events)
    assert events[-1] == {'type': 'end', 'reason': 'error'}


def test_pause_budget_includes_time_while_source_read_is_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, '_CLOUD', CloudConfigStore(tmp_path / 'cloud.bin'))
    monkeypatch.setattr(webui, '_CLOUD_JOBS', {})
    clock = [0.0]
    pause_command_sent = threading.Event()
    pause_accounted = threading.Event()
    worker_ident = []
    source_waiting = threading.Event()
    allow_second = threading.Event()
    second_sent = threading.Event()
    source_waiting_again = threading.Event()
    allow_third = threading.Event()
    sent = []
    audio = b'\x00\x10' * 1600

    class Source:
        def __init__(self, *_args, **_kwargs): worker_ident.append(threading.get_ident())
        def iter_chunks(self):
            yield audio
            source_waiting.set()
            allow_second.wait(3)
            yield audio
            source_waiting_again.set()
            allow_third.wait(3)
            yield audio
        def close(self): pass

    class Engine:
        def transcribe_chunk(self, _audio):
            sent.append(len(sent) + 1)
            if len(sent) == 2:
                second_sent.set()
            return (None, None, '', None)
        def poll(self): return (None, None, '', None)
        def finish(self): return self.poll()
        def close(self): pass

    def session_clock():
        if (pause_command_sent.is_set() and worker_ident
                and threading.get_ident() != worker_ident[0]):
            pause_accounted.set()
        return clock[0]

    monkeypatch.setattr(webui, '_session_monotonic', session_clock)
    monkeypatch.setattr(webui, 'StreamSource', Source)
    monkeypatch.setattr(webui, 'create_engine', lambda *_args, **_kwargs: Engine())
    try:
        with TestClient(webui.app) as client:
            client.put('/api/cloud', json={'api_key': 'test-private-key'})
            ticket = client.post('/api/cloud/sessions', json={'url': 'https://media.test/blocked-source.mp4'}).json()
            with client.websocket_connect('/ws?session=' + ticket['session']) as socket:
                assert source_waiting.wait(2)
                pause_command_sent.set()
                socket.send_text('pause')
                assert pause_accounted.wait(2)
                clock[0] = 20.0
                allow_second.set()
                socket.send_text('resume')
                assert second_sent.wait(2)
                assert source_waiting_again.wait(2)
                assert client.delete('/api/cloud/sessions/' + ticket['session']).json() == {'ok': True}
                allow_third.set()
                events = []
                while True:
                    event = socket.receive_json()
                    events.append(event)
                    if event['type'] == 'end':
                        break
    finally:
        allow_second.set()
        allow_third.set()

    assert sent == [1, 2], sent
    assert not any(event['type'] == 'error' for event in events), events
    assert events[-1] == {'type': 'end', 'reason': 'cancelled'}


def test_only_a_forward_caption_range_refreshes_server_progress_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, '_CLOUD', CloudConfigStore(tmp_path / 'cloud.bin'))
    monkeypatch.setattr(webui, '_CLOUD_JOBS', {})
    monkeypatch.setattr(webui.os.path, 'isfile', lambda _path: True)
    clock = [0.0]
    waiting_second = threading.Event()
    allow_second = threading.Event()
    waiting_third = threading.Event()
    allow_third = threading.Event()
    waiting_fourth = threading.Event()
    allow_fourth = threading.Event()
    sent = []
    audio = b'\x00\x10' * 1600

    class Source:
        def __init__(self, *_args, **_kwargs): pass
        def iter_chunks(self):
            yield audio
            waiting_second.set()
            allow_second.wait(3)
            yield audio
            waiting_third.set()
            allow_third.wait(3)
            yield audio
            waiting_fourth.set()
            allow_fourth.wait(3)
            yield audio
        def close(self): pass

    class Engine:
        def transcribe_chunk(self, _audio):
            sent.append(len(sent) + 1)
            if len(sent) in (2, 3):
                return (0.0, 0.2, '同一字幕范围', None, True, 'same-range')
            return (None, None, '', None)
        def poll(self): return (None, None, '', None)
        def finish(self): return self.poll()
        def close(self): pass

    monkeypatch.setattr(webui, '_session_monotonic', lambda: clock[0])
    monkeypatch.setattr(webui, '_session_wait', lambda stop, _seconds: stop.is_set())
    monkeypatch.setattr(webui, 'StreamSource', Source)
    monkeypatch.setattr(webui, 'create_engine', lambda *_args, **_kwargs: Engine())
    try:
        with TestClient(webui.app) as client:
            client.put('/api/cloud', json={'api_key': 'test-private-key'})
            ticket = client.post('/api/cloud/sessions', json={'url': 'https://media.test/range.mp4'}).json()
            with client.websocket_connect('/ws?session=' + ticket['session']) as socket:
                assert waiting_second.wait(2)
                clock[0] = 14.0
                allow_second.set()
                assert waiting_third.wait(2)
                clock[0] = 28.0
                allow_third.set()
                assert waiting_fourth.wait(2)
                clock[0] = 29.1
                allow_fourth.set()
                events = []
                while True:
                    event = socket.receive_json()
                    events.append(event)
                    if event['type'] == 'end':
                        break
    finally:
        allow_second.set()
        allow_third.set()
        allow_fourth.set()

    assert sent == [1, 2, 3]
    assert [event['end'] for event in events if event['type'] == 'subtitle'] == [0.2, 0.2]
    assert any(event['type'] == 'error' and '15 秒' in event['message'] for event in events)
    assert events[-1] == {'type': 'end', 'reason': 'error'}


@pytest.mark.parametrize('raise_after_first', [False, True])
def test_audio_timeline_error_is_terminal_and_stops_source(
    tmp_path, monkeypatch, raise_after_first
):
    monkeypatch.setattr(webui, '_CLOUD', CloudConfigStore(tmp_path / 'cloud.bin'))
    monkeypatch.setattr(webui, '_CLOUD_JOBS', {})
    monkeypatch.setattr(webui, '_session_wait', lambda *_args: False)
    calls = {'source_created': 0, 'source_closed': 0, 'engine_created': 0,
             'audio_sent': 0, 'engine_closed': 0}
    audio = b'\x00\x10' * 1600

    class Source:
        def __init__(self, *_args, **_kwargs):
            calls['source_created'] += 1

        def iter_chunks(self):
            if raise_after_first:
                yield audio
            raise AudioTimelineError('private decoder detail must not reach the UI')

        def close(self):
            calls['source_closed'] += 1

    class Engine:
        def transcribe_chunk(self, _audio):
            calls['audio_sent'] += 1
            return (None, None, '', None)

        def poll(self):
            return (None, None, '', None)

        def finish(self):
            return self.poll()

        def close(self):
            calls['engine_closed'] += 1

    def create_engine(*_args, **_kwargs):
        calls['engine_created'] += 1
        return Engine()

    monkeypatch.setattr(webui, 'StreamSource', Source)
    monkeypatch.setattr(webui, 'create_engine', create_engine)

    with TestClient(webui.app) as client:
        client.put('/api/cloud', json={'api_key': 'test-private-key'})
        ticket = client.post('/api/cloud/sessions', json={
            'url': 'https://media.test/unsupported-pts.mp4',
        }).json()
        session_stop = webui._CLOUD_JOBS[ticket['session']]['stop']
        with client.websocket_connect('/ws?session=' + ticket['session']) as socket:
            events = []
            while True:
                event = socket.receive_json()
                events.append(event)
                if event['type'] == 'end':
                    break
            assert session_stop.is_set()

    errors = [event for event in events if event['type'] == 'error']
    assert errors == [{
        'type': 'error',
        'message': '音频时间轴存在过大间隙或跳变，已停止识别；可更换来源或仅播放',
        'failure_category': 'media_timeline',
        'retryable': False,
    }]
    assert events[-1] == {'type': 'end', 'reason': 'error'}
    assert calls == {
        'source_created': 1,
        'source_closed': 1,
        'engine_created': int(raise_after_first),
        'audio_sent': int(raise_after_first),
        'engine_closed': int(raise_after_first),
    }
