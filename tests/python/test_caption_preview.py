

from lisca.caption_preview import StableCaptionPreview  # noqa: E402


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


def test_real_unpunctuated_news_prefix_releases_before_sentence_end():
    # Two consecutive real callbacks, 32.819s and 33.519s after startup.
    # The old punctuation-only preview first released this speech at 44.729s.
    text = "There are international far right actors weaponizing the protests"
    words = [(24.41, 24.61, "There"), (24.61, 25.17, " are"),
             (25.17, 25.81, " international"), (25.81, 26.09, " far"),
             (26.09, 26.45, " right"), (26.45, 27.09, " actors"),
             (27.09, 27.41, " weapon"), (27.41, 27.61, "izing"),
             (27.61, 27.93, " the"), (27.93, 28.37, " protests")]
    preview = StableCaptionPreview(24.05)
    assert preview.observe(text, words) == []
    following = words[:-1] + [(27.93, 28.49, " protests"),
                             (28.49, 28.81, " in"), (28.81, 29.09, " France")]
    assert preview.observe(text + " in France", following) == [
        (24.05, 26.45, "There are international far right")]


def test_bounded_preview_keeps_complete_words_and_original_timestamps():
    text = "The weaponizing protests matter"
    words = [(0, .8, "The"), (.8, 2.2, " we"), (2.2, 2.7, "apon"),
             (2.7, 2.9, "izing"), (2.9, 3.4, " protest"),
             (3.4, 3.7, "s"), (3.7, 4.5, " matter")]
    preview = StableCaptionPreview(0)
    assert preview.observe(text, words) == []
    assert preview.observe(text + " later", words + [(4.5, 5, " later")]) == [
        (0, 2.9, "The weaponizing")]


def test_bounded_preview_repeated_updates_do_not_repeat_or_drop_the_tail():
    preview = StableCaptionPreview(0)
    text = "one two three four five six seven eight"
    words = [(i * .75, (i + 1) * .75, (" " if i else "") + word)
             for i, word in enumerate(text.split())]
    assert preview.observe(text, words) == []
    first = preview.observe(text + " nine", words + [(6, 6.75, " nine")])
    assert first
    assert preview.observe(text + " nine", words + [(6, 6.75, " nine")]) == []
    final_text = text + " nine."
    tail = preview.observe(final_text, words + [(6, 6.75, " nine")])
    tail += preview.observe(final_text, words + [(6, 6.75, " nine")])
    assert " ".join(item[2] for item in first + tail) == final_text
    assert all(a[1] == b[0] for a, b in zip(first + tail, (first + tail)[1:]))


def test_correction_of_released_prefix_preserves_uncommitted_words():
    preview = StableCaptionPreview(0)
    words = [(0, .75, "I"), (.75, 1.5, " love"), (1.5, 2.25, " cats"),
             (2.25, 3, " tomorrow")]
    preview.observe("I love cats tomorrow", words)
    assert preview.observe("I love cats tomorrow", words) == [(0, 2.25, "I love cats")]
    revised = "I really love cats tomorrow we travel"
    revised_words = [(0, .2, "I"), (.2, .4, " really"), (.4, 1.1, " love"),
                     (1.1, 2.25, " cats"), (2.25, 3.25, " tomorrow"),
                     (3.25, 4.25, " we"), (4.25, 5.25, " travel")]
    assert preview.observe(revised, revised_words) == []
    assert preview.observe(revised, revised_words) == [(2.25, 4.25, "tomorrow we")]


def test_bounded_preview_requires_matching_word_text_and_complete_lexical_edge():
    preview = StableCaptionPreview(0)
    words = [(0, 1, "inter"), (1, 2, "nation"), (2, 3, "al")]
    assert preview.observe("international", words) == []
    assert preview.observe("internationally", words) == []


def test_unpunctuated_chinese_uses_stable_character_times():
    text = "这是一段没有标点的新闻播报我们继续关注接下来发生的事情以及现场最新的消息"
    words = [(i / 10, (i + 1) / 10, char) for i, char in enumerate(text)]
    preview = StableCaptionPreview(0)
    assert preview.observe(text, words) == []
    assert preview.observe(text, words) == [(0, 2.5, text[:25])]


def test_deletion_in_released_prefix_does_not_consume_pending_tail():
    preview = StableCaptionPreview(0)
    words = [(0, .75, "I"), (.75, 1.5, " love"), (1.5, 2.25, " cats"),
             (2.25, 3, " tomorrow")]
    preview.observe("I love cats tomorrow", words)
    preview.observe("I love cats tomorrow", words)
    revised = "I cats tomorrow we travel"
    revised_words = [(0, .75, "I"), (1.5, 2.25, " cats"),
                     (2.25, 3.25, " tomorrow"), (3.25, 4.25, " we"),
                     (4.25, 5.25, " travel")]
    assert preview.observe(revised, revised_words) == []
    assert preview.observe(revised, revised_words) == [(2.25, 4.25, "tomorrow we")]


def test_real_cfp_correction_cannot_leave_a_partial_word_in_the_pending_tail():
    # Real English callbacks revise "NewsHourF" into "NewsHour. CFP".
    # The consumed F then maps inside CFP; its P must not become a new preview.
    preview = StableCaptionPreview(90.01)
    text = "support PBS NewsHourF professionals are committed"
    words = [(90.01, 90.45, "support"), (90.45, 90.89, " PBS"),
             (90.89, 91.21, " News"), (91.21, 91.69, "Hour"),
             (91.69, 92.21, "F"), (92.21, 92.69, " professionals"),
             (92.69, 92.89, " are"), (92.89, 93.09, " committed")]
    preview.observe(text, words)
    assert preview.observe(text, words) == [(90.01, 92.21, "support PBS NewsHourF")]
    corrected = "support PBS NewsHour. CFP professionals are committed to acting in"
    corrected_words = words[:3] + [(91.21, 91.37, "Hour"),
        (91.702, 92.21, "CFP"), (92.21, 92.69, " professionals"),
        (92.69, 92.89, " are"), (92.89, 93.17, " committed"),
        (93.17, 93.41, " to"), (93.41, 93.69, " acting"),
        (93.69, 93.81, " in")]
    assert preview.observe(corrected, corrected_words) == []
    expanded = corrected + " their clients' best interest"
    expanded_words = corrected_words + [(93.81, 94.05, " their"),
        (94.05, 94.29, " clients"), (94.29, 94.41, "'"),
        (94.41, 94.77, " best"), (94.77, 95.25, " interest")]
    preview.observe(expanded, expanded_words)
    result = preview.observe(expanded, expanded_words)
    assert result and result[0][2].startswith("professionals")


from lisca.caption_preview import TranslationClauses  # noqa: E402


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
