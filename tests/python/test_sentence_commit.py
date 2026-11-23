"""Sentence preparation through the public cloud HTTP/WebSocket session."""

import threading

import pytest
from fastapi.testclient import TestClient

from lisca.cloud_translation import CloudConfigStore
from lisca.web import app as webui


def _session_events(
        tmp_path, monkeypatch, callbacks, *, chunk_sizes=None, observed=None,
        connect_delay=0.0):
    monkeypatch.setattr(webui, "_CLOUD", CloudConfigStore(tmp_path / "cloud.bin"))
    monkeypatch.setattr(webui, "_CLOUD_JOBS", {})
    clock = [0.0]
    monkeypatch.setattr(webui, "_session_monotonic", lambda: clock[0])
    def wait(stop, seconds):
        clock[0] += seconds
        return stop.is_set()
    monkeypatch.setattr(webui, "_session_wait", wait)

    class Source:
        def __init__(self, *_args, **_kwargs): pass
        def iter_chunks(self):
            for size in chunk_sizes or [3200] * len(callbacks):
                yield b"\x00\x10" * (size // 2)
        def close(self): pass

    class Engine:
        def __init__(self): self.callbacks = iter(callbacks)
        def transcribe_chunk(self, _audio):
            at, result = next(self.callbacks)
            if at is not None:
                clock[0] = max(clock[0], at)
            return result
        def poll(self): return (None, None, "", None)
        def finish(self): return self.poll()
        def close(self): pass

    monkeypatch.setattr(webui, "StreamSource", Source)
    monkeypatch.setattr(webui, "create_engine", lambda *_args, **_kwargs: Engine())
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "fixture-key"})
        ticket = client.post("/api/cloud/sessions", json={
            "url": "https://media.test/sentence.mp4",
        })
        assert ticket.status_code == 200
        clock[0] = max(clock[0], connect_delay)
        with client.websocket_connect("/ws?session=" + ticket.json()["session"]) as socket:
            events = []
            while True:
                event = socket.receive_json()
                events.append(event)
                if observed is not None:
                    observed.append((clock[0], event))
                if event["type"] == "end":
                    return events


def _characters(text, duration=4.0):
    return [(index * duration / len(text), (index + 1) * duration / len(text), char)
            for index, char in enumerate(text)]


def test_original_caption_waits_for_the_sentence_instead_of_a_stable_clause(
        tmp_path, monkeypatch):
    # Acceptance: sentence preparation takes priority over a comma/2.5s phrase.
    # Distinguishes early phrase release from the new sentence-first path.
    text = "今天我们关注各地新闻，并了解未来发展趋势和后续安排"
    words = _characters(text)
    events = _session_events(tmp_path, monkeypatch, [
        (0.0, (0.0, 4.0, text, words, False, 7)),
        (0.2, (0.0, 4.0, text, words, False, 7)),
        (0.4, (0.0, 4.0, text + "。", words, True, 7)),
    ])

    assert [event for event in events if event["type"] == "preview"] == []
    assert events[-1] == {"type": "end", "reason": "completed"}


def test_late_first_playable_sentence_commits_before_the_preparation_budget(
        tmp_path, monkeypatch):
    # Acceptance: a withdrawn opening sentence must not give a later first
    # caption a fresh six-second window beyond the session-wide 15-second hold.
    text = "观众朋友您好，欢迎收看今天的新闻"
    words = _characters(text, 2.0)
    observed = []
    _session_events(tmp_path, monkeypatch, [
        (0.0, (3.72, None, "啊", [(3.72, 3.96, "啊")], False, 1)),
        (0.2, (3.92, None, "呃", [(3.92, 4.16, "呃")], False, 1)),
        (7.0, (3.72, 4.16, "", [], True, 1)),
        (11.0, (0.0, None, text, words, False, 7)),
        (11.2, (0.0, None, text, words, False, 7)),
    ], observed=observed, connect_delay=1.0)

    preview_at, preview = next(
        (at, event) for at, event in observed if event["type"] == "preview")
    ready_at, _ = next(
        (at, event) for at, event in observed if event["type"] == "ready")
    assert preview["text"] == text
    assert preview["commit_reason"] == "timeout"
    assert preview_at < 15.0
    assert ready_at < 15.0


def test_stable_sentence_keeps_closing_quote_and_reports_its_commit_identity(
        tmp_path, monkeypatch):
    # Acceptance: stable sentence punctuation differs from provider finality.
    sentence = "她说：“我们关注未来发展趋势。”"
    text = sentence + "接下来继续"
    words = [(0.0, 0.4, "她说"), (0.4, 1.0, "我们关注未来发展"),
             (1.0, 1.2, "趋势"), (1.2, 1.8, "接下来继续")]
    events = _session_events(tmp_path, monkeypatch, [
        (0.0, (0.0, 1.8, text, words, False, 9)),
        (0.2, (0.0, 1.8, text, words, False, 9)),
        (0.4, (0.0, 1.8, text + "。", words, True, 9)),
    ])

    preview, = [event for event in events if event["type"] == "preview"]
    assert preview == {
        "type": "preview", "beg": 0.0, "end": 1.2, "text": sentence,
        "words": [list(word) for word in words[:3]], "sentence_id": 9,
        "commit_id": 0, "revision": 1, "is_final": False,
        "commit_reason": "sentence_end", "source_text": text,
        "source_start": 0, "source_end": len(sentence),
    }
    final = [event for event in events if event["type"] == "subtitle"][-1]
    assert final["is_final"] is True and final["commit_reason"] == "final"


def test_timeout_commits_all_stable_text_without_another_callback_or_source_chunk(
        tmp_path, monkeypatch):
    # Acceptance: the deadline must make a decision while a local read is blocked.
    # A callback-only timeout would wait for the watchdog and fail this case.
    text = "今天我们关注各地新闻并了解未来发展趋势和后续安排"
    words = _characters(text, 2.0)
    monkeypatch.setenv("LISCA_SENTENCE_WAIT_SEC", "0.05")
    monkeypatch.setattr(webui, "_CLOUD", CloudConfigStore(tmp_path / "cloud.bin"))
    monkeypatch.setattr(webui, "_CLOUD_JOBS", {})
    monkeypatch.setattr(webui.os.path, "isfile", lambda _path: True)
    reading = threading.Event()
    release = threading.Event()

    class Source:
        def __init__(self, *_args, **_kwargs): pass
        def iter_chunks(self):
            yield b"\x00\x10" * 16000
            yield b"\x00\x10" * 16000
            reading.set()
            release.wait(2)
        def interrupt(self): release.set()
        def close(self): pass

    class Engine:
        def transcribe_chunk(self, _audio):
            return (0.0, 2.0, text, words, False, 11)
        def poll(self): return (None, None, "", None)
        def finish(self): return self.poll()
        def close(self): pass

    monkeypatch.setattr(webui, "StreamSource", Source)
    monkeypatch.setattr(webui, "create_engine", lambda *_args, **_kwargs: Engine())
    watchdog = threading.Timer(1.0, release.set)
    watchdog.start()
    try:
        with TestClient(webui.app) as client:
            client.put("/api/cloud", json={"api_key": "fixture-key"})
            ticket = client.post("/api/cloud/sessions", json={
                "url": "https://media.test/no-callback.mp4",
            }).json()
            with client.websocket_connect("/ws?session=" + ticket["session"]) as socket:
                events = []
                while True:
                    event = socket.receive_json()
                    events.append(event)
                    if event["type"] in ("ready", "end"):
                        break
                preview, = [event for event in events if event["type"] == "preview"]
                assert reading.is_set() and not release.is_set()
                assert preview["text"] == text
                assert preview["source_text"] == text
                assert (preview["source_start"], preview["source_end"]) == (0, len(text))
                assert preview["commit_reason"] == "timeout" and preview["is_final"] is False
                assert preview["words"] == [list(word) for word in words]
                assert event["type"] == "ready"
                client.delete("/api/cloud/sessions/" + ticket["session"])
                while socket.receive_json()["type"] != "end":
                    pass
    finally:
        watchdog.cancel()
        release.set()


def test_timeout_growth_and_prefix_revision_update_the_same_commit_without_losing_tail(
        tmp_path, monkeypatch):
    # Acceptance: a confirmed insertion revises its old interval and emits only
    # the new tail; repeated callbacks must not create a third copy.
    original = "我们关注发展"
    corrected_prefix = "今天我们关注发展"
    corrected = corrected_prefix + "和后续安排"
    original_words = _characters(original, 1.2)
    corrected_words = _characters(corrected, 2.6)
    events = _session_events(tmp_path, monkeypatch, [
        (0.0, (0.0, 1.2, original, original_words, False, 12)),
        (0.2, (0.0, 1.2, original, original_words, False, 12)),
        (6.1, (None, None, "", None)),
        (6.2, (0.0, 2.6, corrected, corrected_words, False, 12)),
        (6.3, (0.0, 2.6, corrected, corrected_words, False, 12)),
        (6.4, (0.0, 2.6, corrected, corrected_words, False, 12)),
        (6.5, (0.0, 2.6, corrected + "。", corrected_words, True, 12)),
    ])

    previews = [event for event in events if event["type"] == "preview"]
    assert [event["text"] for event in previews] == [original, corrected_prefix, "和后续安排"]
    assert [event["commit_id"] for event in previews] == [0, 0, 1]
    assert [(event["source_start"], event["source_end"]) for event in previews] == [
        (0, 6), (0, 8), (8, 13)]
    assert all(event["source_text"] == corrected for event in previews[1:])
    assert all(event["is_final"] is False for event in previews)
    assert previews[1]["revision"] > previews[0]["revision"]
    assert previews[1]["end"] == previews[2]["beg"] == 1.6
    assert previews[2]["end"] == pytest.approx(2.6)


def test_stable_correction_crossing_a_regrouped_word_keeps_both_commits(
        tmp_path, monkeypatch):
    # The real Chinese long input regrouped a corrected character into 雕像,
    # crossing a released range and blocking every subsequent preview.
    first = "白头海钓雕"
    original = first + "像给习近平"
    corrected = "白头海雕雕像给习近平"
    corrected_words = [(0, .8, "白头海雕"), (.8, 1.2, "雕像"),
                       (1.2, 1.4, "给"), (1.4, 2, "习近平")]
    events = _session_events(tmp_path, monkeypatch, [
        (0.0, (0.0, 1.0, first, _characters(first, 1), False, 8)),
        (0.2, (0.0, 1.0, first, _characters(first, 1), False, 8)),
        (6.1, (None, None, "", None)),
        (6.2, (0.0, 2.0, original, _characters(original, 2), False, 8)),
        (6.3, (0.0, 2.0, original, _characters(original, 2), False, 8)),
        (6.4, (0.0, 2.0, corrected, corrected_words, False, 8)),
        (6.5, (0.0, 2.0, corrected, corrected_words, False, 8)),
        (6.6, (0.0, 2.0, corrected, corrected_words, True, 8)),
    ])
    previews = [event for event in events if event["type"] == "preview"]
    # Reconstruct the public preview just as a consumer applies revisions by ID.
    latest = {event["commit_id"]: event for event in previews}
    parts = sorted(latest.values(), key=lambda event: event["source_start"])
    assert "".join(event["text"] for event in parts) == corrected
    assert sum("雕像" in event["text"] for event in parts) == 1


def test_revised_final_word_with_the_same_end_time_replaces_the_old_preview(
        tmp_path, monkeypatch):
    events = _session_events(tmp_path, monkeypatch, [
        (0.0, (0, 1, "雕", [(0, 1, "雕")], False, 8)),
        (0.2, (0, 1, "雕", [(0, 1, "雕")], False, 8)),
        (6.1, (None, None, "", None)),
        (6.2, (0, 1, "雕像", [(0, 1, "雕像")], False, 8)),
        (6.3, (0, 1, "雕像", [(0, 1, "雕像")], False, 8)),
        (6.4, (0, 1, "雕像", [(0, 1, "雕像")], True, 8)),
    ])
    previews = [event for event in events if event["type"] == "preview"]
    assert any(event["text"] == "雕" for event in previews)
    latest = {event["commit_id"]: event for event in previews}
    assert [event["text"] for event in latest.values() if event["text"]] == ["雕像"]


@pytest.mark.parametrize("first,second,words,state", [
    ("今天继续关注", "明天继续关注", _characters("明天继续关注"), "no_stable_text"),
    ("今天继续关注", "今天继续关注", None, "missing_timing"),
    ("今天继续关注", "今天继续关注", [(0.0, float("nan"), "今天继续关注")], "missing_timing"),
])
def test_timeout_reports_unusable_stable_text_without_inventing_a_preview(
        tmp_path, monkeypatch, first, second, words, state):
    # Acceptance: absent stability and absent usable timing stay distinguishable.
    # A later real final may recover; the expired deadline must not invent one.
    events = _session_events(tmp_path, monkeypatch, [
        (0.0, (0.0, 1.0, first, words, False, 13)),
        (0.2, (0.0, 1.0, second, words, False, 13)),
        (6.1, (None, None, "", None)),
        (6.2, (None, None, "", None)),
        (6.3, (0.0, 1.0, second + "。", None, True, 13)),
    ])

    states = [event for event in events if event.get("caption_state")]
    assert [event["caption_state"] for event in states] == [state]
    assert states[0]["sentence_id"] == 13
    assert not any(event["type"] == "preview" for event in events)
    final = [event for event in events if event["type"] == "subtitle"][-1]
    assert final["is_final"] is True and final["commit_reason"] == "final"


@pytest.mark.parametrize("text", [
    "Dr. Smith measures 3.14 meters",
    "The U.S. team has 2.5 million viewers",
    "A. Smith will explain the next result",
])
def test_decimal_and_abbreviation_periods_do_not_release_incomplete_sentences(
        tmp_path, monkeypatch, text):
    # Acceptance: a repeated decimal/abbreviation is not a ready sentence.
    words = _characters(text)
    events = _session_events(tmp_path, monkeypatch, [
        (0.0, (0.0, 4.0, text, words, False, 14)),
        (0.2, (0.0, 4.0, text, words, False, 14)),
        (0.4, (0.0, 4.0, text + ".", words, True, 14)),
    ])

    assert not any(event["type"] == "preview" for event in events)
    assert [event for event in events if event["type"] == "subtitle"][-1]["is_final"] is True


def test_timeout_is_checked_during_feed_pacing_before_a_later_final_callback(
        tmp_path, monkeypatch):
    # Acceptance: pacing a large PCM chunk cannot silently extend the deadline.
    monkeypatch.setenv("LISCA_SENTENCE_WAIT_SEC", "0.5")
    monkeypatch.setattr(webui.os.path, "isfile", lambda _path: True)
    text = "持续讲话期间完整提交全部稳定内容"
    words = _characters(text, 2.0)
    events = _session_events(tmp_path, monkeypatch, [
        (0.0, (0.0, 2.0, text, words, False, 15)),
        (0.1, (0.0, 2.0, text, words, False, 15)),
        (None, (0.0, 2.0, text + "。", words, True, 15)),
    ], chunk_sizes=[32000, 32000, 320000])

    commits = [event for event in events if event.get("commit_reason")]
    assert [(event["type"], event["commit_reason"]) for event in commits] == [
        ("preview", "timeout"), ("subtitle", "final")]
    assert commits[0]["text"] == text


def test_eof_keeps_the_original_deadline_and_commits_stable_text_as_a_preview(
        tmp_path, monkeypatch):
    # Acceptance: EOF does not claim readiness before unfinished preparation.
    # The provider has stopped producing results, but repeated timed text is usable.
    text = "语音已经结束但识别器没有提交终稿"
    words = _characters(text, 2.0)
    events = _session_events(tmp_path, monkeypatch, [
        (0.0, (0.0, 2.0, text, words, False, 16)),
        (0.2, (0.0, 2.0, text, words, False, 16)),
    ])

    preview, = [event for event in events if event["type"] == "preview"]
    assert preview["text"] == text and preview["commit_reason"] == "timeout"
    assert preview["is_final"] is False
    assert not any(event.get("is_final") for event in events)
    assert events.index(preview) < next(index for index, event in enumerate(events)
                                       if event["type"] == "ready")
    assert events[-1] == {"type": "end", "reason": "completed"}


def test_eof_does_not_complete_with_an_unconfirmed_correction_to_a_released_sentence(
        tmp_path, monkeypatch):
    # Acceptance: EOF must not leave an old preview displayed after its source
    # changes; an unconfirmed correction at the original deadline is incomplete.
    original = "今天我们关注发展趋势"
    corrected = "明天我们关注发展趋势"
    events = _session_events(tmp_path, monkeypatch, [
        (0.0, (0.0, 2.0, original, _characters(original, 2.0), False, 17)),
        (0.2, (0.0, 2.0, original, _characters(original, 2.0), False, 17)),
        (6.1, (None, None, "", None)),
        (6.2, (0.0, 2.0, corrected, _characters(corrected, 2.0), False, 17)),
    ])

    assert [event["text"] for event in events if event["type"] == "preview"] == [original]
    error, = [event for event in events if event["type"] == "error"]
    assert error["failure_category"] == "caption_preparation"
    assert events[-1] == {"type": "end", "reason": "error"}


def test_timeout_holds_an_english_word_until_all_of_its_fragments_are_stable(
        tmp_path, monkeypatch):
    # Acceptance: recognizer fragments inside one spelling are not word boundaries.
    # Keep the incomplete tail pending, then release it without dropping its space.
    text = "Please watch international coverage"
    words = [(0.0, 0.5, "Please"), (0.5, 1.0, "watch"),
             (1.0, 1.5, "inter"), (1.5, 2.0, "national"), (2.0, 2.5, "coverage")]
    events = _session_events(tmp_path, monkeypatch, [
        (0.0, (0.0, 2.0, "Please watch inter", words[:3], False, 18)),
        (0.2, (0.0, 2.5, text, words, False, 18)),
        (6.1, (None, None, "", None)),
        (6.2, (0.0, 2.5, text, words, False, 18)),
        (6.3, (0.0, 2.5, text + ".", words, True, 18)),
    ])

    previews = [event for event in events if event["type"] == "preview"]
    assert [event["text"] for event in previews] == ["Please watch ", "international coverage"]
    assert [(event["beg"], event["end"]) for event in previews] == [(0.0, 1.0), (1.0, 2.5)]
    assert all(event["source_text"] == text for event in previews)
    assert [(event["source_start"], event["source_end"]) for event in previews] == [(0, 13), (13, 35)]


def test_ready_sentence_does_not_report_missing_stable_text_at_the_deadline(
        tmp_path, monkeypatch):
    # Acceptance: the deadline reports a gap only while some content is unprepared.
    text = "字幕已完整准备。"
    words = _characters(text, 2.0)
    events = _session_events(tmp_path, monkeypatch, [
        (0.0, (0.0, 2.0, text, words, False, 19)),
        (0.2, (0.0, 2.0, text, words, False, 19)),
        (6.1, (None, None, "", None)),
        (6.2, (0.0, 2.0, text, words, True, 19)),
    ])

    assert [event["text"] for event in events if event["type"] == "preview"] == [text]
    assert not any(event.get("caption_state") for event in events)
