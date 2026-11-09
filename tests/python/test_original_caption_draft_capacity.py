"""Public cloud HTTP/WebSocket regressions for draft retention capacity."""

from fastapi.testclient import TestClient

from lisca.web import app as webui
from lisca.cloud_translation import CloudConfigStore


def _run_cloud_session(tmp_path, monkeypatch, **scenario):
    monkeypatch.setattr(webui, "_CLOUD", CloudConfigStore(tmp_path / "cloud.bin"))
    monkeypatch.setattr(webui, "_CLOUD_JOBS", {})
    chunk_count = scenario["chunk_count"]
    engine_events = scenario["engine_events"]
    media_url = scenario["media_url"]
    retention_limit = scenario.get("retention_limit")
    if retention_limit is not None:
        monkeypatch.setattr(webui, "_MAX_SESSION_CAPTION_RETENTION_FINGERPRINTS",
                            retention_limit)

    class Source:
        def __init__(self, *_args, **_kwargs): pass
        def iter_chunks(self):
            for _ in range(chunk_count):
                yield b"\x00" * 3200
        def close(self): pass

    class Engine:
        def __init__(self): self.events = iter(engine_events)
        def transcribe_chunk(self, _audio): return next(self.events, (None, None, "", None))
        def poll(self): return (None, None, "", None)
        def finish(self): return self.poll()
        def close(self): pass

    monkeypatch.setattr(webui, "StreamSource", Source)
    monkeypatch.setattr(webui, "create_engine", lambda *_args, **_kwargs: Engine())
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "test-private-key"})
        response = client.post("/api/cloud/sessions", json={"url": media_url})
        assert response.status_code == 200
        with client.websocket_connect("/ws?session=" + response.json()["session"]) as socket:
            events = []
            while True:
                event = socket.receive_json()
                events.append(event)
                if event["type"] == "end":
                    return events


def test_original_caption_accepts_many_live_drafts_before_final(tmp_path, monkeypatch):
    drafts = ["这是一个持续增长的识别草稿第" + str(index) for index in range(1, 13)]
    final_text = "这是一个持续增长的识别草稿最终确认。"
    events = _run_cloud_session(
        tmp_path, monkeypatch, media_url="https://media.test/many-drafts.mp4",
        chunk_count=len(drafts) + 1,
        engine_events=[(0.0, 1.0, text, None, False, 11) for text in drafts]
        + [(0.0, 1.0, final_text, None, True, 11)])

    captions = [event for event in events if event["type"] == "subtitle"]
    assert [event["text"] for event in captions] == drafts + [final_text]
    assert all(event["sentence_id"] == 11 for event in captions)
    assert all(not event["is_final"] for event in captions[:-1])
    assert captions[-1]["is_final"] is True
    assert [event["revision"] for event in captions] == list(range(len(captions)))
    assert not any(event["type"] == "error" for event in events)
    assert events[-1] == {"type": "end", "reason": "completed"}


def test_original_caption_allows_many_anonymous_legacy_finals(tmp_path, monkeypatch):
    captions = [f"匿名兼容句子{index}。" for index in range(1, 13)]
    # The four-field legacy callback has no sentence ID and defaults to final.
    events = _run_cloud_session(
        tmp_path, monkeypatch, media_url="https://media.test/anonymous-legacy-finals.mp4",
        chunk_count=len(captions),
        engine_events=[(index * 1.0, index * 1.0 + 0.8, text, None)
                       for index, text in enumerate(captions)])

    subtitles = [event for event in events if event["type"] == "subtitle"]
    assert [event["text"] for event in subtitles] == captions
    assert all(event["sentence_id"] is None for event in subtitles)
    assert all(event["is_final"] is True for event in subtitles)
    assert all("revision" not in event for event in subtitles)
    assert not any(event["type"] == "error" for event in events)
    assert events[-1] == {"type": "end", "reason": "completed"}


def test_anonymous_previews_do_not_consume_identified_caption_retention_budget(
        tmp_path, monkeypatch):
    repeated_text = "匿名来源中的重复短句。"
    draft = repeated_text + "后续内容仍在识别中"
    repeated_words = [(0.0, 0.4, repeated_text), (0.4, 0.8, "后续内容仍在识别中")]
    shifted_words = [(2.0, 2.4, repeated_text), (2.4, 2.8, "后续内容仍在识别中")]
    events = _run_cloud_session(
        tmp_path, monkeypatch, media_url="https://media.test/anonymous-previews.mp4",
        chunk_count=4, retention_limit=1,
        engine_events=[(0.0, 0.8, draft, repeated_words, False),
                       (0.0, 0.8, draft, repeated_words, False),
                       (2.0, 2.8, draft, shifted_words, False),
                       (2.0, 2.8, draft, shifted_words, False)])

    previews = [event for event in events if event["type"] == "preview"]
    assert [event["text"] for event in previews] == [repeated_text, repeated_text]
    assert [event["beg"] for event in previews] == [0.0, 2.0]
    assert not any(event["type"] == "error" for event in events)
    assert events[-1] == {"type": "end", "reason": "completed"}


def test_withdrawal_suppresses_source_revision_that_released_stable_preview(tmp_path, monkeypatch):
    stable_text = "已经确认的稳定短句。"
    later_draft = stable_text + "后续内容还在修订"
    correction = "已经确认的稳定短句，随后重新修正。"
    words = [(0.0, 0.2, "已经"), (0.2, 0.5, "确认的"),
             (0.5, 0.8, "稳定"), (0.8, 1.1, "短句。")]

    engine_events = [
        (0.0, 1.1, stable_text, words, False, 21),
        (0.0, 1.1, stable_text, words, False, 21),
        (0.0, 1.5, later_draft, words, False, 21),
        (0.0, 1.5, "", None, True, 21),
        (0.0, 1.1, stable_text, words, False, 21),
        (0.0, 1.1, stable_text, words, False, 21),
        (0.0, 1.5, correction, words, True, 21),
    ]
    events = _run_cloud_session(
        tmp_path, monkeypatch, media_url="https://media.test/withdrawn-preview.mp4",
        chunk_count=7, engine_events=engine_events)

    subtitles = [event for event in events if event["type"] == "subtitle"]
    previews = [event for event in events if event["type"] == "preview"]
    removals = [event for event in events if event["type"] == "caption_remove"]
    assert [event["text"] for event in subtitles] == [stable_text, stable_text,
                                                        later_draft, correction]
    assert [event["text"] for event in previews] == [stable_text]
    assert len(removals) == 1 and removals[0]["sentence_id"] == 21
    assert events[-1] == {"type": "end", "reason": "completed"}


def test_long_sentence_can_release_many_stable_previews_before_final(tmp_path, monkeypatch):
    clauses = [f"这是第{index}个稳定短句。" for index in range(1, 22)]
    draft = "".join(clauses)
    final_text = draft + "最终确认。"
    words = [(index * 0.2, (index + 1) * 0.2, clause)
             for index, clause in enumerate(clauses)]

    events = _run_cloud_session(
        tmp_path, monkeypatch, media_url="https://media.test/long-sentence.mp4",
        chunk_count=3,
        engine_events=[(0.0, 4.2, draft, words, False, 31),
                       (0.0, 4.2, draft, words, False, 31),
                       (0.0, 4.4, final_text, words, True, 31)])

    previews = [event for event in events if event["type"] == "preview"]
    captions = [event for event in events if event["type"] == "subtitle"]
    assert [event["text"] for event in previews] == clauses
    assert [event["text"] for event in captions] == [draft, draft, final_text]
    assert captions[-1]["is_final"] is True
    assert not any(event["type"] == "error" for event in events)
    assert events[-1] == {"type": "end", "reason": "completed"}


def test_retained_preview_budget_stops_incomplete_instead_of_evicting(tmp_path, monkeypatch):
    clauses = ["这是第一条稳定短句。", "这是第二条稳定短句。", "这是第三条稳定短句。"]
    draft = "".join(clauses)
    words = [(index * 0.2, (index + 1) * 0.2, clause)
             for index, clause in enumerate(clauses)]

    events = _run_cloud_session(
        tmp_path, monkeypatch, media_url="https://media.test/retention-limit.mp4",
        chunk_count=2, retention_limit=3,
        engine_events=[(0.0, 0.6, draft, words, False, 41),
                       (0.0, 0.6, draft, words, False, 41)])

    previews = [event for event in events if event["type"] == "preview"]
    errors = [event for event in events if event["type"] == "error"]
    assert [event["text"] for event in previews] == clauses[:2]
    assert errors and errors[0]["failure_category"] == "capacity"
    assert events[-1] == {"type": "end", "reason": "error"}
