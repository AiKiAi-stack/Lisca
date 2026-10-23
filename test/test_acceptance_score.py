"""Keep release accuracy scoring independent of provider output formatting."""
import pytest

from score_original_acceptance import edit_distance, score, tokens


def test_written_numbers_are_not_silently_equated_with_spelled_numbers():
    assert tokens("Don't close ３ windows!", "en") == ["dont", "close", "3", "windows"]
    assert tokens("别 太 装。", "zh") == list("别太装")
    assert edit_distance(["three"], ["3"]) == 1
    assert edit_distance(["a", "b", "c"], ["a", "d", "e", "c"]) == 2


def test_retracted_and_revised_final_captions_do_not_duplicate_the_score():
    manifest = {"dataset": "fixed", "config": "en", "sha256": "test", "seconds": 1,
                "items": [{"reference": "Keep these words"}]}
    events = [{"type": "subtitle", "sentence_id": 1, "beg": 0, "is_final": True, "text": "Bad"},
              {"type": "caption_remove", "sentence_id": 1},
              {"type": "subtitle", "sentence_id": 2, "beg": 0, "is_final": True, "text": "Keep"},
              {"type": "subtitle", "sentence_id": 2, "beg": 0, "is_final": True, "text": "Keep these words"},
              {"type": "end"}]
    result = score(manifest, {"events": events, "errors": []}, "en")
    assert result["error_rate"] == 0
    assert result["transcript"] == "Keep these words"
    assert result["accuracy_gate_passed"]
    assert not score(manifest, {"events": events, "errors": ["stopped early"]}, "en")["accuracy_gate_passed"]


@pytest.mark.parametrize("tail", [[], [{"type": "error", "message": "stopped early"}, {"type": "end"}]])
def test_incomplete_or_failed_event_stream_cannot_pass_without_an_error_summary(tail):
    manifest = {"dataset": "fixed", "config": "en", "sha256": "test", "seconds": 1,
                "items": [{"reference": "Keep these words"}]}
    events = [{"type": "subtitle", "sentence_id": 1, "beg": 0, "is_final": True,
               "text": "Keep these words"}, *tail]
    result = score(manifest, {"events": events}, "en")
    assert result["error_rate"] == 0
    assert not result["session_without_errors"]
    assert not result["accuracy_gate_passed"]


def test_captions_with_null_sentence_ids_remain_distinct_by_timestamp():
    manifest = {"dataset": "fixed", "config": "en", "sha256": "test", "seconds": 2,
                "items": [{"reference": "Keep these words"}]}
    events = [{"type": "subtitle", "sentence_id": None, "beg": 0, "is_final": True, "text": "Keep"},
              {"type": "subtitle", "sentence_id": None, "beg": 1, "is_final": True, "text": "these words"},
              {"type": "end"}]
    result = score(manifest, {"events": events}, "en")
    assert result["transcript"] == "Keep these words"
    assert result["accuracy_gate_passed"]


def test_anonymous_withdrawal_removes_anonymous_captions_but_keeps_identified_ones():
    manifest = {"dataset": "fixed", "config": "en", "sha256": "test", "seconds": 2,
                "items": [{"reference": "Keep these words"}]}
    events = [{"type": "subtitle", "sentence_id": None, "beg": 0, "is_final": True, "text": "Wrong"},
              {"type": "subtitle", "sentence_id": None, "beg": 1, "is_final": True, "text": "also wrong"},
              {"type": "subtitle", "sentence_id": 1, "beg": 0, "is_final": True, "text": "Keep these words"},
              {"type": "caption_remove", "sentence_id": None}, {"type": "end"}]
    result = score(manifest, {"events": events}, "en")
    assert result["transcript"] == "Keep these words"
    assert result["reference_sha256"] == "test"
    assert "input_sha256" not in result
    assert result["accuracy_gate_passed"]


@pytest.mark.parametrize("summary", [{}, {"translated_cues": 0}])
def test_translation_in_events_cannot_be_hidden_by_the_summary(summary):
    manifest = {"dataset": "fixed", "config": "en", "sha256": "test", "seconds": 1,
                "items": [{"reference": "Keep these words"}]}
    events = [{"type": "subtitle", "sentence_id": 1, "beg": 0, "is_final": True,
               "text": "Keep these words", "translated": True}, {"type": "end"}]
    result = score(manifest, {"events": events, **summary}, "en")
    assert result["translated_cues"] == 1
    assert not result["accuracy_gate_passed"]
