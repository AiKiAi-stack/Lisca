"""Prepare repeated, timed ASR text for sentence or legacy phrase commits."""

from difflib import SequenceMatcher
import math
import re


_CLAUSE_END = re.compile(r"[，,。.!?！？；;]")
_SENTENCE_END = re.compile(r"[。.!?！？]+[\"'”’）)\]】]*")
_SPOKEN = re.compile(r"[^\w]", re.UNICODE)
_ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs",
                  "etc", "fig", "no", "approx", "inc", "ltd"}


def _sentence_ending(text, match):
    """Conservative punctuation signal; ambiguous periods wait for final/timeout."""
    if text[match.start()] != ".":
        return True
    position = match.start()
    if (position and text[position - 1].isdigit()
            and position + 1 < len(text) and text[position + 1].isdigit()):
        return False
    if position + 1 < len(text) and re.match(r"[A-Za-z0-9.]", text[position + 1]):
        return False
    word = re.search(r"([A-Za-z]+(?:\.[A-Za-z]+)*)\.$", text[:position + 1])
    if word:
        token = word.group(1)
        if token.lower() in _ABBREVIATIONS or all(len(part) == 1 for part in token.split(".")):
            return False
    return True


def _splits_word(text, position):
    following = text[position:]
    return ((re.match(r"\w", following)
             and not re.search(r"[\u3400-\u9fff]", text[position - 1:position + 1]))
            or re.match(r"['’ʼ‐‑‒–—-]\w", following)
            or (position == len(text) and re.match(r"\w", text[position - 1:position])
                and not re.search(r"[\u3400-\u9fff]", text[position - 1:position])))


class StableCaptionPreview:
    def __init__(self, begin: float, *, sentence_first: bool = False):
        self.begin = begin
        self.sentence_first = sentence_first
        self.previous_text = ""
        self.emitted_chars = 0
        self.emitted_end = begin
        self.commits = []
        self.sequence = 0
        self.stable_chars = 0
        self.words = []
        self._word_spans = []
        self.released = []
        self.pending_corrections = set()
        self._tail_was_revised = False
        self.withheld_word_tail = False

    def observe(self, text: str, words: list | None) -> list[tuple[float, float, str]]:
        """Return stable clauses or bounded word phrases with ASR timing."""
        self.commits = []
        common = 0
        for left, right in zip(self.previous_text, text):
            if left != right:
                break
            common += 1
        self._tail_was_revised = bool(
            self.sentence_first and self.previous_text and common
            and common < len(self.previous_text)
            and len(text) <= len(self.previous_text))
        self.words = [(beg, end, str(token)) for beg, end, token in words or []
                      if isinstance(beg, (int, float)) and isinstance(end, (int, float))
                      and math.isfinite(beg) and math.isfinite(end)
                      and end > beg and token]
        if self.emitted_chars and self.previous_text[:self.emitted_chars] != text[:self.emitted_chars]:
            # Final replaces corrected previews; remap the consumed prefix so
            # an earlier insertion/deletion cannot swallow the pending tail.
            changes = SequenceMatcher(None, self.previous_text, text, autojunk=False).get_opcodes()

            def mapped(position):
                for tag, old_start, old_end, new_start, new_end in changes:
                    if old_start <= position <= old_end:
                        return (new_start + position - old_start if tag == "equal" else
                                new_start if position == old_start else new_end)
                return len(text)

            self.emitted_chars = mapped(self.emitted_chars)
            # A corrected spelling may grow around the consumed boundary.
            # Consume its remainder with that old word, never emit a subword.
            for word in re.finditer(r"\w+(?:['’ʼ‐‑-]\w+)*", text):
                if (word.start() < self.emitted_chars < word.end()
                        and not re.search(r"[\u3400-\u9fff]", word.group())):
                    self.emitted_chars = word.end()
                    break
            if self.sentence_first:
                for released in self.released:
                    start = 0 if released["start"] == 0 else mapped(released["start"])
                    finish = mapped(released["finish"])
                    if released is self.released[-1]:
                        finish = self.emitted_chars
                    corrected = text[start:finish]
                    if (start, finish, corrected) != (
                            released["start"], released["finish"], released["text"]):
                        self.pending_corrections.add(released["commit_id"])
                        released.update(start=start, finish=finish, text=corrected)
        self.previous_text = text
        self.stable_chars = common
        results = []
        if self.sentence_first:
            self._word_spans = self._source_word_spans()
            safe_releases = self._align_released_ranges(common)
            for index, released in enumerate(self.released):
                if index not in safe_releases or released["finish"] > common:
                    continue
                timing, range_words = self._range_data(released["start"], released["finish"])
                if timing is None and released["text"]:
                    continue
                changed = timing is not None and (
                    timing != (released["beg"], released["end"]) or range_words != released["words"])
                if not changed and released["commit_id"] not in self.pending_corrections:
                    continue
                if timing is not None:
                    released["beg"], released["end"] = timing
                released["words"] = range_words
                results.append((released["beg"], released["end"], released["text"]))
                self.commits.append(self._metadata(released))
                self.pending_corrections.discard(released["commit_id"])
                if released is self.released[-1]:
                    self.emitted_end = released["end"]
        if common <= self.emitted_chars or not self.words:
            return results

        timed_words = self.words

        endings = _SENTENCE_END if self.sentence_first else _CLAUSE_END
        for match in endings.finditer(text, self.emitted_chars):
            boundary = match.end()
            if boundary > common:
                break
            if self.sentence_first and not _sentence_ending(text, match):
                continue
            if self.sentence_first:
                boundary = self._complete_word_boundary(boundary, common)
                if boundary is None:
                    continue
            phrase = text[self.emitted_chars:boundary]
            if not self.sentence_first:
                phrase = phrase.strip()
            if len(_SPOKEN.sub("", phrase)) < (1 if self.sentence_first else 3):
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
            if self.sentence_first:
                self._record(self.emitted_end, phrase_end, boundary, "sentence_end")
            self.emitted_chars = boundary
            self.emitted_end = phrase_end
        # A long monologue may have no punctuation for tens of seconds. Use
        # roughly 2.5s of repeated speech, leaving short clauses to punctuation.
        while not self.sentence_first and (boundary := self._bounded_boundary(text, timed_words, common)):
            finish, phrase_end = boundary
            phrase = text[self.emitted_chars:finish].strip()
            results.append((self.emitted_end, phrase_end, phrase))
            self.emitted_chars = finish
            self.emitted_end = phrase_end
        return results

    def commit_stable(self) -> list[tuple[float, float, str]]:
        """Commit the entire timed stable prefix when the session deadline expires."""
        self.commits = []
        self.withheld_word_tail = False
        text = self.previous_text
        positions = [match.end() for match in re.finditer(r"\w", text)]
        spoken = _SPOKEN.sub("", text)
        timed = _SPOKEN.sub("", "".join(token for _, _, token in self.words))
        if not timed or not spoken.startswith(timed):
            return []
        seen = 0
        boundary = self.emitted_chars
        phrase_end = self.emitted_end
        timed_boundaries = []
        for _, end, token in self.words:
            seen += len(_SPOKEN.sub("", token))
            if not seen:
                continue
            finish = positions[seen - 1]
            if finish > self.stable_chars:
                break
            if _splits_word(text, finish):
                self.withheld_word_tail = finish == len(text)
                continue
            if finish > boundary and end > phrase_end:
                boundary, phrase_end = finish, end
                timed_boundaries.append((boundary, phrase_end))
        if boundary <= self.emitted_chars:
            return []
        if (boundary == self.stable_chars and self._tail_was_revised
                and self._ends_with_unsettled_cjk(boundary)):
            # A provider can revise "明年" down to "明" while keeping the next
            # characters pending. The surviving one-character token may be the
            # start of a revised word, so its timeout page is not safe to show.
            boundary -= 1
            prior = [item for item in timed_boundaries if item[0] <= boundary]
            if not prior or prior[-1][1] <= self.emitted_end:
                return []
            phrase_end = prior[-1][1]
        # Keep stable punctuation/spacing with its speech, without guessing a time.
        while boundary < self.stable_chars and not re.match(r"\w", text[boundary]):
            boundary += 1
        self._record(self.emitted_end, phrase_end, boundary, "timeout")
        result = [(self.emitted_end, phrase_end, text[self.emitted_chars:boundary])]
        self.emitted_chars, self.emitted_end = boundary, phrase_end
        return result

    def _ends_with_unsettled_cjk(self, boundary):
        if boundary <= 0 or not re.match(r"[\u3400-\u9fff]", self.previous_text[boundary - 1]):
            return False
        token = next((word for _, right, word in self._word_spans if right == boundary),
                     None)
        return (token is not None
                and len(_SPOKEN.sub("", token[2])) == 1
                and bool(re.fullmatch(r"[\u3400-\u9fff]", token[2])))

    def _record(self, beg, end, finish, reason):
        _, words = self._range_data(self.emitted_chars, finish)
        released = {"commit_id": self.sequence, "commit_reason": reason,
            "start": self.emitted_chars, "finish": finish,
            "text": self.previous_text[self.emitted_chars:finish], "beg": beg, "end": end,
            "words": words}
        self.sequence += 1
        self.released.append(released)
        self.commits.append(self._metadata(released))

    def _metadata(self, released):
        return {"commit_id": released["commit_id"], "commit_reason": released["commit_reason"],
            "source_text": self.previous_text,
            "source_start": released["start"], "source_end": released["finish"]}

    def words_for_commit(self, commit_id):
        """Return the current source-aligned word spans for a released commit."""
        return self.released[commit_id]["words"]

    def _source_word_spans(self):
        text = self.previous_text
        spoken = _SPOKEN.sub("", text)
        timed = _SPOKEN.sub("", "".join(token for _, _, token in self.words))
        if not timed or not spoken.startswith(timed):
            return []
        positions = [match.start() for match in re.finditer(r"\w", text)]
        spans = []
        seen = 0
        for word in self.words:
            count = len(_SPOKEN.sub("", word[2]))
            if count:
                spans.append((positions[seen], positions[seen + count - 1] + 1, word))
                seen += count
        return spans

    def _range_data(self, start, finish):
        # Only observed whole words can give a revised commit its playback interval.
        words = [word for left, right, word in self._word_spans if start <= left and right <= finish]
        spoken = _SPOKEN.sub("", self.previous_text[start:finish])
        if not words or _SPOKEN.sub("", "".join(word[2] for word in words)) != spoken:
            return None, []
        beg = self.begin if start == 0 else words[0][0]
        end = words[-1][1]
        return ((beg, end), words) if end > beg else (None, [])

    def _align_released_ranges(self, stable_chars):
        if not self.released:
            return set()
        boundaries = [self.released[0]["start"]]
        boundaries.extend(released["finish"] for released in self.released)
        blocked = set()
        for word_start, word_finish, _ in self._word_spans:
            crossing = [index for index, boundary in enumerate(boundaries)
                        if word_start < boundary < word_finish]
            if not crossing:
                continue
            if word_finish > stable_chars:
                blocked.update(crossing)
            elif crossing == [len(self.released)]:
                boundaries[-1] = word_finish
            else:
                first = crossing[0]
                start = boundaries[first - 1] if first else word_start
                prefix = self.previous_text[start:word_start]
                boundaries[first] = word_start if _SPOKEN.sub("", prefix) else start
                for index in crossing[1:]:
                    boundaries[index] = word_finish
        safe = set()
        for index, released in enumerate(self.released):
            if index in blocked or index + 1 in blocked:
                continue
            start, finish = boundaries[index:index + 2]
            corrected = self.previous_text[start:finish]
            if (start, finish, corrected) != (
                    released["start"], released["finish"], released["text"]):
                self.pending_corrections.add(released["commit_id"])
                released.update(start=start, finish=finish, text=corrected)
            safe.add(index)
        if len(boundaries) - 1 not in blocked:
            self.emitted_chars = boundaries[-1]
        return safe

    def _complete_word_boundary(self, boundary, stable_chars):
        for word_start, word_finish, _ in self._word_spans:
            if word_start < boundary < word_finish:
                return word_finish if word_finish <= stable_chars else None
        return boundary

    def _bounded_boundary(self, text, words, common):
        spoken_positions = [match.end() for match in re.finditer(r"\w", text)]
        if _SPOKEN.sub("", "".join(token for _, _, token in words)) != _SPOKEN.sub("", text):
            return None
        seen = 0
        pending = []
        for beg, end, token in words:
            count = len(_SPOKEN.sub("", token))
            seen += count
            if not count:
                continue
            finish = spoken_positions[seen - 1]
            if self.emitted_chars < finish <= common and end > self.emitted_end:
                pending.append((finish, beg, end))
        if not pending or pending[-1][2] - pending[0][1] < 2.5:
            return None
        candidates = []
        for finish, _, end in pending:
            if finish == len(text):
                continue
            # ASR tokens can split a spelling, contraction or hyphenated word.
            if _splits_word(text, finish):
                continue
            if len(_SPOKEN.sub("", text[self.emitted_chars:finish])) < 3:
                continue
            candidates.append((finish, end))
        near = [item for item in candidates if 2.0 <= item[1] - pending[0][1] <= 2.5]
        if near:
            return near[-1]
        # One complete lexical word can cross the target; never cut its tokens.
        return next((item for item in candidates if item[1] - pending[0][1] >= 2.5), None)


class TranslationClauses:
    """Commit repeated short source phrases once; never translate token revisions."""

    def __init__(self, begin):
        self.begin = begin
        self.previous = ""
        self.committed = 0
        self.sequence = 0
        self.last_end = begin
        self.released = []
        self.pending_corrections = set()

    def observe(self, text, words=None, is_final=False, end=None):
        results = []
        if self.committed and self.previous[:self.committed] != text[:self.committed]:
            changes = SequenceMatcher(None, self.previous, text, autojunk=False).get_opcodes()

            def mapped(position):
                for tag, old_start, old_end, new_start, new_end in changes:
                    if old_start <= position <= old_end:
                        if tag == "equal":
                            return new_start + position - old_start
                        return new_start if position == old_start else new_end
                return len(text)

            # ASR may correct an already-stable prefix. Map released boundaries
            # so an insertion/deletion cannot silently eat the uncommitted tail.
            for clause in self.released:
                start = 0 if clause["start"] == 0 else mapped(clause["start"])
                finish = mapped(clause["finish"])
                corrected = text[start:finish].strip()
                if corrected != clause["text"]:
                    self.pending_corrections.add(clause["sequence"])
                    clause["text"] = corrected
                clause["start"], clause["finish"] = start, finish
            self.committed = mapped(self.committed)
        common = 0
        for left, right in zip(self.previous, text):
            if left != right:
                break
            common += 1
        self.previous = text
        limit = len(text) if is_final else common
        for clause in self.released:
            if clause["sequence"] in self.pending_corrections and (is_final or (common > 0 and clause["finish"] <= limit)):
                results.append((clause["beg"], clause["end"], clause["text"], clause["sequence"]))
                self.pending_corrections.remove(clause["sequence"])
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
                self.released.append({"start": self.committed, "finish": absolute,
                    "text": phrase, "beg": self.last_end, "end": phrase_end,
                    "sequence": self.sequence})
                self.sequence += 1
            self.committed = absolute
            if phrase_end is not None:
                self.last_end = phrase_end
        return results
