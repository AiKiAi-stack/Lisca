import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools"))

from caption_preview import StableCaptionPreview  # noqa: E402


def test_repeated_partial_releases_complete_clause_with_word_timing():
    preview = StableCaptionPreview(20.8)
    words = [(20.8 + i * 0.15, 20.95 + i * 0.15, char)
             for i, char in enumerate("今天我们一起看比赛")]
    assert preview.observe("今天我们一起看比赛，", words) == []
    assert preview.observe("今天我们一起看比赛，接下来", words) == [
        (20.8, words[-1][1], "今天我们一起看比赛，")]
    assert preview.emitted_end == words[-1][1]


def test_changed_partial_does_not_release_uncertain_words():
    preview = StableCaptionPreview(0.0)
    words = [(i * 0.1, (i + 1) * 0.1, char) for i, char in enumerate("今天下雨了")]
    preview.observe("今天下雨了，", words)
    assert preview.observe("今天下雪了，", words) == []


from caption_preview import TranslationClauses  # noqa: E402


def test_translation_releases_stable_clause_without_word_times_and_final_only_tail():
    collector = TranslationClauses(0)
    assert collector.observe("Hello world. Next") == []
    assert collector.observe("Hello world. Next sentence") == [(0, None, "Hello world.", 0)]
    assert collector.observe("Hello world. Next sentence.", is_final=True, end=5) == [
        (0, 5, "Next sentence.", 1)]


def test_fast_speech_without_punctuation_releases_bounded_repeated_phrases():
    collector = TranslationClauses(0)
    text = "这是一段没有标点但是说得很快的持续讲话内容" * 8
    assert collector.observe(text) == []
    clauses = collector.observe(text + "结束")
    assert clauses and all(3 <= len(item[2]) <= 28 for item in clauses)
    tail = collector.observe(text + "结束", is_final=True, end=20)
    assert "".join(item[2] for item in clauses + tail) == text + "结束"


def test_uncertain_source_revisions_do_not_emit_token_captions():
    collector = TranslationClauses(0)
    assert collector.observe("It is raining.") == []
    assert collector.observe("It is snowing.") == []
    assert collector.observe("It is snowing. Today") == [(0, None, "It is snowing.", 0)]


def test_correction_after_release_updates_whole_clause_and_preserves_remaining_tail():
    collector = TranslationClauses(0)
    collector.observe("I love cats. Tomorrow")
    first = collector.observe("I love cats. Tomorrow we travel")
    assert first == [(0, None, "I love cats.", 0)]
    revised = collector.observe("I really love cats. Tomorrow we travel.", is_final=True, end=6)
    assert [item[2] for item in revised] == ["I really love cats.", "Tomorrow we travel."]
    assert [item[3] for item in revised] == [0, 1]


def test_a_correction_must_repeat_or_be_final_before_retranslation():
    collector = TranslationClauses(0)
    collector.observe("I love cats. Tomorrow")
    collector.observe("I love cats. Tomorrow we travel")
    assert collector.observe("I really love cats. Tomorrow we travel") == []
    assert collector.observe("I really love cats. Tomorrow we travel soon") == [
        (0, None, "I really love cats.", 0)]


def test_deletion_of_released_clause_retracts_its_translation_and_preserves_tail():
    collector = TranslationClauses(0)
    collector.observe("I love cats. Tomorrow")
    collector.observe("I love cats. Tomorrow we travel")
    result = collector.observe("Tomorrow we travel.", is_final=True, end=6)
    assert [item[2] for item in result] == ["", "Tomorrow we travel."]
    assert [item[3] for item in result] == [0, 1]


def test_empty_final_hypothesis_retracts_every_released_clause():
    collector = TranslationClauses(0)
    collector.observe("I love cats.")
    collector.observe("I love cats.")
    assert collector.observe("", is_final=True, end=2) == [(0, None, "", 0)]
