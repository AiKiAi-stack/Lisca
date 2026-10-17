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
