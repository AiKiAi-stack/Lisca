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
