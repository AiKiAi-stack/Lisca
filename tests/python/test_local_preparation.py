"""Local preparation and bounded feeding through public HTTP/WebSocket sessions."""

import json
from pathlib import Path
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
    sent_before_delete = None
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
    assert max(seconds - wall for wall, seconds in feed[:208]) <= 10.0 + 1e-6
    assert feed[0][0] == pytest.approx(0.0)
    assert len(feed) == sent_before_delete == 210
    assert max(seconds - wall for wall, seconds in feed[208:]) <= 2.0 + 1e-6
    assert peak_backlog[0] <= 1
    assert set(closed) == {'source', 'engine'}
    assert events[-1] == {'type': 'end', 'reason': 'cancelled'}


@pytest.mark.parametrize('duration_sec', [26, 1802])
def test_saved_opening_drafts_prepare_all_stable_text_before_fifteen_seconds_and_eof(
        tmp_path, monkeypatch, duration_sec, record_property):
    history = json.loads((Path(__file__).parents[1] / 'acceptance' / 'fixtures'
                          / 'local-opening-history.json').read_text())
    callbacks = history['callbacks']
    offset = history['estimated_source_offset_sec']
    monkeypatch.setattr(webui, '_CLOUD', CloudConfigStore(tmp_path / 'cloud.bin'))
    monkeypatch.setattr(webui, '_CLOUD_JOBS', {})
    clock = [0.0]
    generated = [0]
    fed = [0.0]
    feed, delivered, closed = [], [], []
    peak_backlog = [0]
    ready_received = threading.Event()
    allow_after_ready = threading.Event()
    steady_feed_complete = threading.Event()
    release_cancelled_feed = threading.Event()
    media = tmp_path / 'same-opening.mp4'
    media.touch()
    audio = b'\x00\x10' * 1600

    class Source:
        def __init__(self, *_args, **_kwargs): pass
        def iter_chunks(self):
            for _ in range(duration_sec * 10):
                generated[0] += 1
                yield audio
        def close(self): closed.append('source')

    class Engine:
        def transcribe_chunk(self, pcm):
            fed[0] = round(fed[0] + len(pcm) / 16000, 6)
            feed.append((clock[0], fed[0], ready_received.is_set()))
            peak_backlog[0] = max(peak_backlog[0], generated[0] - len(feed))
            if sum(steady for _, _, steady in feed) == 2:
                steady_feed_complete.set()
                assert release_cancelled_feed.wait(3)
            return self.poll()
        def poll(self):
            if len(delivered) == len(callbacks):
                return (None, None, '', None)
            item = callbacks[len(delivered)]
            # Input position is a fake delivery model; saved wall times are metadata.
            if fed[0] + 1e-6 < max(word[1] for word in item['words']) + offset:
                return (None, None, '', None)
            delivered.append((clock[0], item['text']))
            words = [(beg + offset, end + offset, text)
                     for beg, end, text in item['words']]
            return (item['beg'] + offset, None, item['text'], words,
                    item['is_final'], item['sentence_id'])
        def finish(self): return self.poll()
        def close(self): closed.append('engine')

    def advance_clock(stop, seconds):
        if (delivered and clock[0] + 1e-6 >= delivered[0][0] + 6
                and not ready_received.is_set()):
            assert allow_after_ready.wait(3), 'no ready event after the sentence deadline'
        clock[0] += seconds
        return stop.is_set()

    monkeypatch.setattr(webui, '_session_monotonic', lambda: clock[0])
    monkeypatch.setattr(webui, '_session_wait', advance_clock)
    monkeypatch.setattr(webui, 'StreamSource', Source)
    monkeypatch.setattr(webui, 'create_engine', lambda *_args, **_kwargs: Engine())
    ready_at = None
    sent_before_delete = None
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
                        assert generated[0] < duration_sec * 10
                        ready_received.set()
                        allow_after_ready.set()
                        assert steady_feed_complete.wait(3)
                        sent_before_delete = len(feed)
                        assert client.delete('/api/cloud/sessions/' + session).json() == {'ok': True}
                        release_cancelled_feed.set()
                    if event['type'] == 'end':
                        break
    finally:
        allow_after_ready.set()
        release_cancelled_feed.set()

    assert ready_at is not None
    assert ready_at < 15.0
    assert ready_at == pytest.approx(delivered[0][0] + 6.0, abs=0.051)
    prepared = [event for event in events if event['type'] == 'preview']
    assert len(prepared) == 1
    assert prepared[0]['text'] == '观众朋友您好，欢迎收看9月15号的公视晚间新闻，我是王佩涵'
    assert prepared[0]['source_text'] == callbacks[-1]['text']
    assert prepared[0]['commit_reason'] == 'timeout'
    assert prepared[0]['is_final'] is False
    assert len(delivered) == 10
    assert not any(event['type'] == 'ready' and event.get('silent') for event in events)
    assert feed[19][:2] == pytest.approx((0.0, 2.0))
    assert feed[20][:2] == pytest.approx((0.05, 2.1))
    assert feed[59][:2] == pytest.approx((2.0, 6.0))
    assert feed[99][:2] == pytest.approx((4.0, 10.0))
    assert feed[179][:2] == pytest.approx((8.0, 18.0))
    assert max(seconds - wall for wall, seconds, _ in feed) <= 10.0 + 1e-6
    assert all(seconds - wall <= 2.0 + 1e-6 for wall, seconds, steady in feed if steady)
    assert len(feed) == sent_before_delete
    assert peak_backlog[0] <= 1
    assert set(closed) == {'source', 'engine'}
    assert events[-1] == {'type': 'end', 'reason': 'cancelled'}
    record_property('local_preparation', json.dumps({
        'duration_sec': duration_sec, 'first_draft_at': delivered[0][0],
        'last_saved_draft_at': delivered[-1][0], 'ready_at': ready_at,
        'max_source_lead_sec': max(seconds - wall for wall, seconds, _ in feed),
        'max_steady_source_lead_sec': max(seconds - wall for wall, seconds, steady in feed if steady),
        'peak_decoded_backlog': peak_backlog[0], 'sent_chunks': len(feed),
    }))


def test_ready_during_a_local_feed_wait_applies_two_second_limit_to_the_pending_chunk(
        tmp_path, monkeypatch, record_property):
    monkeypatch.setattr(webui, '_CLOUD', CloudConfigStore(tmp_path / 'cloud.bin'))
    monkeypatch.setattr(webui, '_CLOUD_JOBS', {})
    clock = [0.0]
    feed = []
    final_at = [None]
    ready_received = threading.Event()
    allow_after_ready = threading.Event()
    steady_feed_complete = threading.Event()
    release_cancelled_feed = threading.Event()
    media = tmp_path / 'asynchronous-final.mp4'
    media.touch()
    audio = b'\x00\x10' * 1600

    class Source:
        def __init__(self, *_args, **_kwargs): pass
        def iter_chunks(self):
            for _ in range(300):
                yield audio
        def close(self): pass

    class Engine:
        def transcribe_chunk(self, _pcm):
            feed.append((clock[0], (len(feed) + 1) / 10, final_at[0] is not None))
            if sum(steady for _, _, steady in feed) == 2:
                steady_feed_complete.set()
                assert release_cancelled_feed.wait(3)
            if len(feed) == 5:
                return (0.3, None, '等待这句的终稿', [(0.3, 0.5, '等待这句的终稿')], False, 1)
            return self.poll()
        def poll(self):
            if len(feed) >= 5 and clock[0] + 1e-6 >= 2.5 and final_at[0] is None:
                final_at[0] = clock[0]
                return (0.3, 0.5, '等待这句的终稿。',
                        [(0.3, 0.5, '等待这句的终稿')], True, 1)
            return (None, None, '', None)
        def finish(self): return self.poll()
        def close(self): pass

    def advance_clock(stop, seconds):
        if final_at[0] is not None and not ready_received.is_set():
            assert allow_after_ready.wait(3), 'an asynchronous final did not publish ready'
        clock[0] += seconds
        return stop.is_set()

    monkeypatch.setattr(webui, '_session_monotonic', lambda: clock[0])
    monkeypatch.setattr(webui, '_session_wait', advance_clock)
    monkeypatch.setattr(webui, 'StreamSource', Source)
    monkeypatch.setattr(webui, 'create_engine', lambda *_args, **_kwargs: Engine())
    sent_before_delete = None
    try:
        with TestClient(webui.app) as client:
            client.put('/api/cloud', json={'api_key': 'fixture-key'})
            session = client.post('/api/cloud/sessions', json={'url': str(media)}).json()['session']
            with client.websocket_connect('/ws?session=' + session) as socket:
                events = []
                while True:
                    event = socket.receive_json()
                    events.append(event)
                    if event['type'] == 'ready':
                        ready_received.set()
                        allow_after_ready.set()
                        assert steady_feed_complete.wait(3)
                        sent_before_delete = len(feed)
                        assert client.delete('/api/cloud/sessions/' + session).json() == {'ok': True}
                        release_cancelled_feed.set()
                    if event['type'] == 'end':
                        break
    finally:
        allow_after_ready.set()
        release_cancelled_feed.set()

    assert final_at[0] == pytest.approx(2.5)
    assert any(seconds - wall > 2 for wall, seconds, final in feed if not final)
    after_final = [(wall, seconds) for wall, seconds, final in feed if final]
    assert len(after_final) == 2
    assert all(seconds - wall <= 2.0 + 1e-6 for wall, seconds in after_final)
    assert len(feed) == sent_before_delete
    assert events[-1] == {'type': 'end', 'reason': 'cancelled'}
    record_property('pending_chunk_after_ready', json.dumps({
        'final_at': final_at[0], 'feeds_after_final': after_final,
    }))


def test_ready_recovery_wait_drains_the_next_sentence_before_feeding_more_audio(
        tmp_path, monkeypatch, record_property):
    monkeypatch.setattr(webui, '_CLOUD', CloudConfigStore(tmp_path / 'cloud.bin'))
    monkeypatch.setattr(webui, '_CLOUD_JOBS', {})
    clock = [0.0]
    feed, delivered = [], []
    draft_observed = threading.Event()
    timeout_observed = threading.Event()
    media = tmp_path / 'next-sentence-during-recovery.mp4'
    media.touch()
    audio = b'\x00\x10' * 1600
    callbacks = [
        (8.05, (16.9, 17.8, '第一句需要终稿。', [(16.9, 17.8, '第一句需要终稿')], True, 1)),
        (8.1, (17.8, None, '第二句仍在准备', [(17.8, 18.0, '第二句仍在准备')], False, 2)),
        (8.2, (17.8, None, '第二句仍在准备', [(17.8, 18.0, '第二句仍在准备')], False, 2)),
    ]

    class Source:
        def __init__(self, *_args, **_kwargs): pass
        def iter_chunks(self):
            for _ in range(300):
                yield audio
        def close(self): pass

    class Engine:
        def transcribe_chunk(self, _pcm):
            feed.append((clock[0], (len(feed) + 1) / 10))
            if len(feed) == 178:
                return (16.9, None, '第一句需要终稿',
                        [(16.9, 17.8, '第一句需要终稿')], False, 1)
            return self.poll()
        def poll(self):
            if len(feed) < 180 or len(delivered) == len(callbacks):
                return (None, None, '', None)
            due, result = callbacks[len(delivered)]
            if clock[0] + 1e-6 < due:
                return (None, None, '', None)
            delivered.append(clock[0])
            return result
        def finish(self): return self.poll()
        def close(self): pass

    def advance_clock(stop, seconds):
        if len(delivered) >= 2 and not draft_observed.is_set():
            assert draft_observed.wait(3), 'the next draft was not observed'
        if (len(delivered) >= 2 and clock[0] + 1e-6 >= delivered[1] + 6
                and not timeout_observed.is_set()):
            assert timeout_observed.wait(3), 'the next sentence missed its original deadline'
        clock[0] += seconds
        return stop.is_set()

    monkeypatch.setattr(webui, '_session_monotonic', lambda: clock[0])
    monkeypatch.setattr(webui, '_session_wait', advance_clock)
    monkeypatch.setattr(webui, 'StreamSource', Source)
    monkeypatch.setattr(webui, 'create_engine', lambda *_args, **_kwargs: Engine())
    observed = {}
    try:
        with TestClient(webui.app) as client:
            client.put('/api/cloud', json={'api_key': 'polling-fixture'})
            session = client.post('/api/cloud/sessions', json={'url': str(media)}).json()['session']
            with client.websocket_connect('/ws?session=' + session) as socket:
                events = []
                try:
                    while True:
                        event = socket.receive_json()
                        events.append(event)
                        if (event['type'] == 'subtitle' and event.get('sentence_id') == 2
                                and not draft_observed.is_set()):
                            observed['draft_at'] = clock[0]
                            assert clock[0] == pytest.approx(8.1, abs=0.051)
                            assert len(feed) == 180
                            assert any(item['type'] == 'ready' for item in events)
                            draft_observed.set()
                        if event['type'] == 'preview' and event.get('sentence_id') == 2:
                            observed['timeout_at'] = clock[0]
                            assert clock[0] == pytest.approx(14.1, abs=0.051)
                            assert len(feed) == 180
                            assert event['text'] == '第二句仍在准备'
                            assert event['commit_reason'] == 'timeout'
                            assert event['is_final'] is False
                            break
                        if event['type'] == 'end':
                            pytest.fail('session ended before the next sentence was prepared')
                finally:
                    assert client.delete('/api/cloud/sessions/' + session).json() == {'ok': True}
                    draft_observed.set()
                    timeout_observed.set()
                while events[-1]['type'] != 'end':
                    events.append(socket.receive_json())
    finally:
        draft_observed.set()
        timeout_observed.set()

    assert len(feed) == 180
    assert feed[-1] == pytest.approx((8.0, 18.0))
    assert delivered == pytest.approx([8.05, 8.1, 8.2])
    assert events[-1] == {'type': 'end', 'reason': 'cancelled'}
    record_property('next_sentence_during_ready_recovery', json.dumps({
        **observed, 'callback_times': delivered, 'last_feed': feed[-1],
        'sent_chunks': len(feed),
    }))
