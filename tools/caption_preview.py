"""Release complete, repeated clauses from a long live ASR partial.

The recognizer may keep one sentence open for tens of seconds. Waiting for its
final event would freeze the video even after several complete clauses have
already appeared in successive partial results.
"""

import re


_CLAUSE_END = re.compile(r"[，,。.!?！？；;]")
_SPOKEN = re.compile(r"[^\w]", re.UNICODE)


class StableCaptionPreview:
    def __init__(self, begin: float):
        self.begin = begin
        self.previous_text = ""
        self.emitted_chars = 0
        self.emitted_end = begin

    def observe(self, text: str, words: list | None) -> list[tuple[float, float, str]]:
        """Return clauses unchanged in two results, with ASR word timing."""
        common = 0
        for left, right in zip(self.previous_text, text):
            if left != right:
                break
            common += 1
        self.previous_text = text
        if common <= self.emitted_chars or not words:
            return []

        timed_words = [(beg, end, str(token)) for beg, end, token in words
                       if isinstance(beg, (int, float)) and isinstance(end, (int, float))
                       and end > beg and token]
        if not timed_words:
            return []

        results = []
        for match in _CLAUSE_END.finditer(text, self.emitted_chars, common):
            boundary = match.end()
            phrase = text[self.emitted_chars:boundary].strip()
            if len(_SPOKEN.sub("", phrase)) < 3:
                continue
            spoken_chars = len(_SPOKEN.sub("", text[:boundary]))
            seen = 0
            phrase_end = None
            for _, end, token in timed_words:
                seen += len(_SPOKEN.sub("", token))
                if seen >= spoken_chars:
                    phrase_end = end
                    break
            if phrase_end is None or phrase_end <= self.emitted_end:
                continue
            results.append((self.emitted_end, phrase_end, phrase))
            self.emitted_chars = boundary
            self.emitted_end = phrase_end
        return results


class TranslationClauses:
    """Commit repeated short source phrases once; never translate token revisions."""

    def __init__(self, begin):
        self.begin = begin
        self.previous = ""
        self.committed = 0
        self.sequence = 0
        self.last_end = begin

    def observe(self, text, words=None, is_final=False, end=None):
        common = 0
        for left, right in zip(self.previous, text):
            if left != right:
                break
            common += 1
        self.previous = text
        limit = len(text) if is_final else common
        results = []
        while self.committed < limit:
            rest = text[self.committed:limit]
            # Prefer a complete clause. Bound unpunctuated fast speech as well.
            punctuation = _CLAUSE_END.search(rest)
            boundary = punctuation.end() if punctuation else 0
            cjk = bool(re.search(r"[\u3400-\u9fff]", rest))
            maximum = 28 if cjk else 100
            if not boundary or boundary > maximum:
                if len(rest) >= maximum:
                    boundary = maximum if cjk else rest.rfind(" ", 0, maximum + 1)
                    if boundary <= 0:
                        boundary = maximum
                elif is_final:
                    boundary = len(rest)
                else:
                    break
            phrase = rest[:boundary].strip()
            absolute = self.committed + boundary
            # Avoid tiny fragments such as a decimal point on their own.
            if not is_final and len(_SPOKEN.sub("", phrase)) < 3:
                break
            phrase_end = None
            spoken = len(_SPOKEN.sub("", text[:absolute]))
            seen = 0
            for word_beg, word_end, token in words or []:
                del word_beg
                seen += len(_SPOKEN.sub("", str(token)))
                if seen >= spoken and isinstance(word_end, (int, float)):
                    phrase_end = word_end
                    break
            if phrase_end is None and is_final and absolute == len(text):
                phrase_end = end
            if phrase:
                results.append((self.last_end, phrase_end, phrase, self.sequence))
                self.sequence += 1
            self.committed = absolute
            if phrase_end is not None:
                self.last_end = phrase_end
        return results
