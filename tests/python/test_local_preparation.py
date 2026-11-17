"""Local preparation and bounded feeding through public HTTP/WebSocket sessions."""

import threading

import pytest
from fastapi.testclient import TestClient

from lisca.cloud_translation import CloudConfigStore
from lisca.web import app as webui


def test_long_local_file_prepares_late_body_within_fifteen_seconds_then_bounds_steady_feed(
        tmp_path, monkeypatch):
    monkeypatch.setattr(webui, '_CLOUD', CloudConfigStore(tmp_path / 'cloud.bin'))
    monkeypatch.setattr(webui, '_CLOUD_JOBS', {})
    clock = [0.0]
    generated = [0]
    fed = [0.0]
    feed = []
    peak_backlog = [0]
    allow_after_ready = threading.Event()
    steady_feed_complete = threading.Event()
    release_cancelled_read = threading.Event()
    closed = []
    audio = b'\x00\x10' * 1600  # Non-silent 100ms PCM; no silence readiness.
    media = tmp_path / 'long-news.mp4'
    media.touch()

    class Source:
        def __init__(self, *_args, **_kwargs): pass
        def iter_chunks(self):
            for index in range(1802 * 10):
                generated[0] += 1
                if index == 208:
                    assert allow_after_ready.wait(3)
                if index == 210:
                    steady_feed_complete.set()
                    assert release_cancelled_read.wait(3)
                yield audio
        def close(self): closed.append('source')

    class Engine:
        def transcribe_chunk(self, pcm):
            fed[0] += len(pcm) / 16000
            feed.append((clock[0], fed[0]))
            peak_backlog[0] = max(peak_backlog[0], generated[0] - len(feed))
            if len(feed) == 184:
                return (18.0, None, '观众', [(18.0, 18.4, '观众')], False, 1)
            if len(feed) == 208:
                return (18.0, 20.8, '观众朋友您好。',
                        [(18.0, 18.4, '观众'), (18.4, 19.0, '朋友'),
                         (19.0, 20.8, '您好')], True, 1)
            return self.poll()
        def poll(self): return (None, None, '', None)
        def finish(self): return self.poll()
        def close(self): closed.append('engine')

    def advance_clock(stop, seconds):
        clock[0] += seconds
        return stop.is_set()

    monkeypatch.setattr(webui, '_session_monotonic', lambda: clock[0])
    monkeypatch.setattr(webui, '_session_wait', advance_clock)
    monkeypatch.setattr(webui, 'StreamSource', Source)
    monkeypatch.setattr(webui, 'create_engine', lambda *_args, **_kwargs: Engine())
    ready_at = None
    try:
        with TestClient(webui.app) as client:
            client.put('/api/cloud', json={'api_key': 'fixture-key'})
            response = client.post('/api/cloud/sessions', json={'url': str(media)})
            assert response.status_code == 200
            session = response.json()['session']
            with client.websocket_connect('/ws?session=' + session) as socket:
                events = []
                while True:
                    event = socket.receive_json()
                    events.append(event)
                    if event['type'] == 'ready':
                        ready_at = clock[0]
                        assert generated[0] < 1802 * 10
                        assert len(feed) == 208
                        allow_after_ready.set()
                        assert steady_feed_complete.wait(3)
                        sent_before_delete = len(feed)
                        assert client.delete('/api/cloud/sessions/' + session).json() == {'ok': True}
                        release_cancelled_read.set()
                    if event['type'] == 'end':
                        break
    finally:
        allow_after_ready.set()
        release_cancelled_read.set()

    assert ready_at is not None, 'late body must prepare before the first-preparation deadline'
    assert ready_at < 15.0
    assert any(event['type'] == 'subtitle' and event.get('is_final')
               and event['text'] == '观众朋友您好。' for event in events)
    assert not any(event['type'] == 'ready' and event.get('silent') for event in events)
    assert max(seconds - wall for wall, seconds in feed[:208]) <= 6.0 + 1e-6
    assert feed[0][0] == pytest.approx(0.0)
    assert len(feed) == sent_before_delete == 210
    assert max(seconds - wall for wall, seconds in feed[208:]) <= 2.0 + 1e-6
    assert peak_backlog[0] <= 1
    assert set(closed) == {'source', 'engine'}
    assert events[-1] == {'type': 'end', 'reason': 'cancelled'}
