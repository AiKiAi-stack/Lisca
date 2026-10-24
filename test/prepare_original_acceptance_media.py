#!/usr/bin/env python3
"""Prepare small, pinned, single-speaker acceptance media from public corpora.

The tool downloads only the selected utterances. It never calls ASR and never
persists Hugging Face Dataset Viewer signed URLs. Run from any directory:

    python test/prepare_original_acceptance_media.py

Source cache defaults to an ext4 directory outside the repository and may be
overridden with ORIGINAL_ACCEPTANCE_MEDIA_CACHE.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import av

REPO = Path(__file__).resolve().parents[1]
CACHE_ROOT = Path(os.environ.get(
    "ORIGINAL_ACCEPTANCE_MEDIA_CACHE",
    "/home/wbb/.local/share/stream-with-asr/media-source-cache",
))
OUTPUT_DIR = REPO / "uploads" / "acceptance-media"
MANIFEST_PATH = REPO / "docs" / "research" / "single-speaker-acceptance-media-2026-10-24.json"

AISHELL_REPO = "AISHELL/AISHELL-3"
AISHELL_REV = "f20d5db4a31fe779ef07bb1af4ea92da5c786622"
AISHELL_CARD = "https://huggingface.co/datasets/AISHELL/AISHELL-3"
AISHELL_LICENSE = "https://www.apache.org/licenses/LICENSE-2.0"

LIBRI_REPO = "openslr/librispeech_asr"
LIBRI_REV = "71cacbfb7e2354c4226d01e70d77d5fca3d04ba1"
LIBRI_CARD = "https://huggingface.co/datasets/openslr/librispeech_asr"
LIBRI_SOURCE = "https://www.openslr.org/12"
LIBRI_LICENSE = "https://creativecommons.org/licenses/by/4.0/"

TARGET_SPEECH_SECONDS = 300
FAST_WINDOW_SECONDS = 60
SAMPLE_RATE = 16_000
SILENCE_BETWEEN_UTTERANCES_SECONDS = 0.25
SILENCE_FRAMES = int(SAMPLE_RATE * SILENCE_BETWEEN_UTTERANCES_SECONDS)
REQUEST_TIMEOUT_SECONDS = 25
MAX_ATTEMPTS = 2
TRANSFER_BUDGET_BYTES = 500_000_000
MAX_JSON_BYTES = 3_000_000
MAX_AISHELL_CONTENT_BYTES = 8_000_000
MAX_ONE_AUDIO_BYTES = 12_000_000
USER_AGENT = "stream-with-asr-original-caption-acceptance-media/1.0"


class PreparationError(RuntimeError):
    pass


@dataclass
class TransferBudget:
    transferred: int = 0

    def account(self, amount: int) -> None:
        self.transferred += amount
        if self.transferred > TRANSFER_BUDGET_BYTES:
            raise PreparationError("download budget exceeded (500 MB per run)")


@dataclass
class Clip:
    language: str
    corpus: str
    source_id: str
    speaker_id: str
    chapter_id: str | None
    text: str
    source_path: str
    source_sha256: str
    source_bytes: int
    source_sample_rate: int
    source_sample_count: int
    pcm_s16le_16k: bytes
    expected_git_blob_oid: str | None = None

    @property
    def output_samples(self) -> int:
        return len(self.pcm_s16le_16k) // 2

    @property
    def duration_seconds(self) -> float:
        return self.output_samples / SAMPLE_RATE

    @property
    def text_units(self) -> int:
        if self.language == "en":
            return len(self.text.split())
        return sum(1 for char in self.text if "\u3400" <= char <= "\u9fff")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def safe_fetch(url: str, budget: TransferBudget, *, label: str, max_bytes: int) -> bytes:
    """Fetch with bounded retries/time/size; never expose the URL in errors."""
    last_status: int | None = None
    for attempt in range(MAX_ATTEMPTS):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        received = bytearray()
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    last_status = status
                    raise PreparationError(f"{label}: HTTP {status}")
                declared = response.headers.get("Content-Length")
                if declared and int(declared) > max_bytes:
                    raise PreparationError(f"{label}: response exceeds {max_bytes} byte limit")
                while True:
                    remaining = min(64 * 1024, max_bytes + 1 - len(received))
                    if remaining <= 0:
                        raise PreparationError(f"{label}: response exceeds {max_bytes} byte limit")
                    chunk = response.read(remaining)
                    if not chunk:
                        break
                    received.extend(chunk)
                    budget.account(len(chunk))
                    if len(received) > max_bytes:
                        raise PreparationError(f"{label}: response exceeds {max_bytes} byte limit")
            if declared and len(received) != int(declared):
                raise PreparationError(f"{label}: response length did not match Content-Length")
            return bytes(received)
        except PreparationError:
            raise
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code is not None:
                last_status = int(code)
                if 400 <= int(code) < 500 and int(code) not in (408, 429):
                    break
            if attempt + 1 < MAX_ATTEMPTS:
                time.sleep(1)
    suffix = f" (HTTP {last_status})" if last_status is not None else ""
    raise PreparationError(f"{label}: request failed after {MAX_ATTEMPTS} bounded attempts{suffix}")


def fetch_cached(path: Path, url: str, budget: TransferBudget, *, label: str, max_bytes: int) -> bytes:
    if path.is_file():
        data = path.read_bytes()
        if len(data) <= max_bytes:
            return data
        raise PreparationError(f"cached {label} exceeds {max_bytes} byte limit")
    data = safe_fetch(url, budget, label=label, max_bytes=max_bytes)
    atomic_write(path, data)
    return data


def fetch_json(url: str, budget: TransferBudget, *, label: str) -> dict[str, Any]:
    body = safe_fetch(url, budget, label=label, max_bytes=MAX_JSON_BYTES)
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreparationError(f"{label}: invalid JSON response") from exc
    if not isinstance(value, dict):
        raise PreparationError(f"{label}: JSON response is not an object")
    return value


def decode_to_mono_16k(raw: bytes, label: str) -> tuple[bytes, int, int]:
    try:
        container = av.open(__import__("io").BytesIO(raw), mode="r")
        audio_stream = next(stream for stream in container.streams if stream.type == "audio")
        source_rate = int(audio_stream.rate or audio_stream.codec_context.sample_rate or 0)
        if source_rate <= 0:
            raise PreparationError(f"{label}: no valid source sample rate")
        source_samples = 0
        output: list[bytes] = []
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        for frame in container.decode(audio_stream):
            source_samples += int(frame.samples)
            for converted in resampler.resample(frame):
                output.append(converted.to_ndarray().astype("<i2", copy=False).tobytes())
        for converted in resampler.resample(None):
            output.append(converted.to_ndarray().astype("<i2", copy=False).tobytes())
        container.close()
    except PreparationError:
        raise
    except Exception as exc:
        raise PreparationError(f"{label}: source audio could not be decoded") from exc
    pcm = b"".join(output)
    if source_samples <= 0 or not pcm or len(pcm) % 2:
        raise PreparationError(f"{label}: decoded audio is empty or malformed")
    return pcm, source_rate, source_samples


def cached_clip_path(corpus: str, revision: str, source_id: str, suffix: str) -> Path:
    return CACHE_ROOT / corpus / revision / "selected-audio" / f"{source_id}{suffix}"


def get_clip_bytes(cache_path: Path, url: str | None, budget: TransferBudget, *, label: str) -> bytes:
    if cache_path.is_file():
        data = cache_path.read_bytes()
        if not data or len(data) > MAX_ONE_AUDIO_BYTES:
            raise PreparationError(f"cached {label} is empty or exceeds per-file limit")
        return data
    if not url:
        raise PreparationError(f"{label}: source URL unavailable for uncached audio")
    data = safe_fetch(url, budget, label=label, max_bytes=MAX_ONE_AUDIO_BYTES)
    atomic_write(cache_path, data)
    return data


def make_clip(
    *, language: str, corpus: str, source_id: str, speaker_id: str,
    chapter_id: str | None, text: str, source_path: str, raw: bytes,
    expected_git_blob_oid: str | None = None,
) -> Clip:
    if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        if corpus == "librispeech":
            # LibriSpeech test-clean source files are FLAC; the viewer response
            # may relay FLAC or a decoded WAV depending on the serving backend.
            pass
        else:
            raise PreparationError(f"{source_id}: AISHELL source is not a WAV container")
    pcm, source_rate, source_samples = decode_to_mono_16k(raw, source_id)
    return Clip(
        language=language,
        corpus=corpus,
        source_id=source_id,
        speaker_id=speaker_id,
        chapter_id=chapter_id,
        text=text,
        source_path=source_path,
        source_sha256=sha256(raw),
        source_bytes=len(raw),
        source_sample_rate=source_rate,
        source_sample_count=source_samples,
        pcm_s16le_16k=pcm,
        expected_git_blob_oid=expected_git_blob_oid,
    )


def aishell_metadata(budget: TransferBudget) -> tuple[dict[str, tuple[str, str, str]], list[tuple[str, str]], str, str, str]:
    root = CACHE_ROOT / "aishell3" / AISHELL_REV
    base = f"https://huggingface.co/datasets/{AISHELL_REPO}/resolve/{AISHELL_REV}"
    spk_path = root / "spk-info.txt"
    train_path = root / "train_content.txt"
    spk_data = fetch_cached(spk_path, f"{base}/spk-info.txt", budget, label="AISHELL speaker metadata", max_bytes=50_000)
    content_data = fetch_cached(train_path, f"{base}/train/content.txt", budget, label="AISHELL train transcripts", max_bytes=MAX_AISHELL_CONTENT_BYTES)
    speakers: dict[str, tuple[str, str, str]] = {}
    for line in spk_data.decode("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) >= 4:
            speakers[fields[0]] = (fields[1], fields[2], fields[3])
    entries: list[tuple[str, str]] = []
    for line in content_data.decode("utf-8").splitlines():
        if "\t" not in line:
            continue
        filename, token_line = line.split("\t", 1)
        tokens = token_line.split()
        transcript = "".join(tokens[index] for index in range(0, len(tokens), 2))
        if filename.lower().endswith(".wav") and transcript:
            entries.append((filename, transcript))
    if not speakers or not entries:
        raise PreparationError("AISHELL metadata did not contain speakers and transcript rows")
    return speakers, entries, sha256(spk_data), sha256(content_data), f"{base}/train/content.txt"


def aishell_file_oids(speaker: str, budget: TransferBudget) -> dict[str, str]:
    url = f"https://huggingface.co/api/datasets/{AISHELL_REPO}/tree/{AISHELL_REV}/train/wav/{speaker}?recursive=true&expand=false&limit=1000"
    obj = safe_fetch(url, budget, label="AISHELL selected speaker file list", max_bytes=1_000_000)
    try:
        rows = json.loads(obj)
    except json.JSONDecodeError as exc:
        raise PreparationError("AISHELL selected speaker file list is invalid JSON") from exc
    result: dict[str, str] = {}
    for row in rows:
        path = row.get("path", "")
        if row.get("type") == "file" and path.startswith(f"train/wav/{speaker}/"):
            result[Path(path).name] = str(row.get("oid", ""))
    return result


def load_librispeech_catalog(budget: TransferBudget) -> tuple[list[dict[str, Any]], str]:
    cache_path = CACHE_ROOT / "librispeech" / LIBRI_REV / "test-clean-rows.json"
    if cache_path.is_file():
        try:
            rows = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise PreparationError("cached LibriSpeech row catalog is invalid") from exc
        if not isinstance(rows, list) or not rows:
            raise PreparationError("cached LibriSpeech row catalog is empty")
        return rows, sha256(cache_path.read_bytes())

    rows: list[dict[str, Any]] = []
    for offset in range(0, 2620, 100):
        length = min(100, 2620 - offset)
        query = urllib.parse.urlencode({"dataset": LIBRI_REPO, "config": "all", "split": "test.clean", "offset": offset, "length": length})
        page = fetch_json(f"https://datasets-server.huggingface.co/rows?{query}", budget, label=f"LibriSpeech metadata page {offset}")
        for envelope in page.get("rows", []):
            row = envelope.get("row", {})
            rows.append({
                "row_idx": int(envelope.get("row_idx", offset + len(rows))),
                "id": str(row.get("id", "")),
                "speaker_id": int(row.get("speaker_id")),
                "chapter_id": int(row.get("chapter_id")),
                "text": str(row.get("text", "")),
            })
    if len(rows) != 2620:
        raise PreparationError(f"LibriSpeech metadata row count changed: {len(rows)}")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(rows, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    atomic_write(cache_path, encoded)
    return rows, sha256(encoded)


def get_librispeech_viewer_urls(target_rows: list[dict[str, Any]], budget: TransferBudget) -> dict[str, str]:
    # Verify the mutable dataset-server view still points at the pinned corpus
    # commit before asking it for temporary, in-memory audio URLs.
    refs = fetch_json(f"https://huggingface.co/api/datasets/{LIBRI_REPO}/refs", budget, label="LibriSpeech revision check")
    main = next((branch for branch in refs.get("branches", []) if branch.get("name") == "main"), None)
    if not main or main.get("targetCommit") != LIBRI_REV:
        raise PreparationError("LibriSpeech main no longer matches the pinned revision; stop and re-pin deliberately")

    target_by_idx = {int(row["row_idx"]): row for row in target_rows}
    page_offsets = sorted({(row_idx // 100) * 100 for row_idx in target_by_idx})
    result: dict[str, str] = {}
    for offset in page_offsets:
        length = min(100, 2620 - offset)
        query = urllib.parse.urlencode({"dataset": LIBRI_REPO, "config": "all", "split": "test.clean", "offset": offset, "length": length})
        page = fetch_json(f"https://datasets-server.huggingface.co/rows?{query}", budget, label=f"LibriSpeech selected metadata page {offset}")
        for envelope in page.get("rows", []):
            idx = int(envelope.get("row_idx", -1))
            if idx not in target_by_idx:
                continue
            row = envelope.get("row", {})
            expected = target_by_idx[idx]
            if (str(row.get("id")) != expected["id"]
                    or int(row.get("speaker_id")) != int(expected["speaker_id"])
                    or int(row.get("chapter_id")) != int(expected["chapter_id"])
                    or str(row.get("text")) != expected["text"]):
                raise PreparationError(f"LibriSpeech row {idx} no longer matches the pinned sanitized catalog")
            audio = row.get("audio")
            if not isinstance(audio, list) or len(audio) != 1 or not isinstance(audio[0].get("src"), str):
                raise PreparationError(f"LibriSpeech row {expected['id']}: missing viewer audio source")
            src = audio[0]["src"]
            # Never persist or log src: it is a short-lived signed URL.
            src_path = urllib.parse.unquote(urllib.parse.urlsplit(src).path)
            if LIBRI_REV not in src_path:
                raise PreparationError(f"LibriSpeech row {expected['id']}: audio URL does not identify pinned revision")
            result[expected["id"]] = src
    missing = {row["id"] for row in target_by_idx.values()} - set(result)
    if missing:
        raise PreparationError("LibriSpeech viewer omitted one or more selected audio rows")
    return result


def select_minimal_window(clips: list[Clip], minimum_seconds: float) -> tuple[list[Clip], float]:
    """For every start, take the shortest contiguous window reaching duration.

    Rank those windows by transcript units per source-audio second, with earliest
    start breaking ties. This produces only a listening candidate, never a
    declaration that a human confirmed fast speech.
    """
    best: tuple[float, int, int] | None = None
    for start in range(len(clips)):
        seconds = 0.0
        units = 0
        end = start
        while end < len(clips) and seconds < minimum_seconds:
            seconds += clips[end].duration_seconds
            units += clips[end].text_units
            end += 1
        if seconds < minimum_seconds or seconds <= 0:
            continue
        rate = units / seconds
        candidate = (rate, -start, end)
        if best is None or candidate > best:
            best = candidate
    if best is None:
        raise PreparationError(f"fixed reference sample has no {minimum_seconds:g}-second fast-candidate window")
    rate, neg_start, end = best
    start = -neg_start
    return clips[start:end], rate


def write_asset(clips: list[Clip], path: Path) -> dict[str, Any]:
    if not clips:
        raise PreparationError(f"cannot write empty asset: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    starts: list[int] = []
    cursor = 0
    inserted_frames = 0
    source_samples = 0
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        for index, clip in enumerate(clips):
            starts.append(cursor)
            output.writeframesraw(clip.pcm_s16le_16k)
            cursor += clip.output_samples
            source_samples += clip.output_samples
            if index + 1 < len(clips):
                output.writeframesraw(b"\0\0" * SILENCE_FRAMES)
                cursor += SILENCE_FRAMES
                inserted_frames += SILENCE_FRAMES
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    utterances: list[dict[str, Any]] = []
    cursor = 0
    for index, clip in enumerate(clips):
        begin = cursor
        end = begin + clip.output_samples
        utterances.append({
            "source_id": clip.source_id,
            "chapter_id": clip.chapter_id,
            "text": clip.text,
            "source_sha256": clip.source_sha256,
            "source_bytes": clip.source_bytes,
            "source_path": clip.source_path,
            "expected_git_blob_oid": clip.expected_git_blob_oid,
            "source_sample_rate_hz": clip.source_sample_rate,
            "source_sample_count": clip.source_sample_count,
            "source_audio_seconds": round(clip.source_sample_count / clip.source_sample_rate, 9),
            "converted_frames_16khz": clip.output_samples,
            "converted_audio_seconds": round(clip.duration_seconds, 9),
            "asset_start_frame": begin,
            "asset_end_frame_exclusive": end,
        })
        cursor = end + (SILENCE_FRAMES if index + 1 < len(clips) else 0)
    frames = cursor
    return {
        "file": str(path.relative_to(REPO)),
        "sha256": content_hash,
        "format": "WAV PCM signed 16-bit little-endian, mono, 16000 Hz",
        "frame_count": frames,
        "duration_seconds": round(frames / SAMPLE_RATE, 9),
        "source_utterance_audio_seconds": round(source_samples / SAMPLE_RATE, 9),
        "source_utterance_frames_16khz": source_samples,
        "inserted_silence_seconds": round(inserted_frames / SAMPLE_RATE, 9),
        "inserted_silence_frames": inserted_frames,
        "source_audio_duration_note": "Source utterance audio duration, including any natural pauses inside source clips; no VAD/phonation segmentation was applied.",
        "utterance_count": len(clips),
        "utterances": utterances,
    }


def ordered_clip_digest(clips: list[Clip]) -> str:
    digest = hashlib.sha256()
    for clip in clips:
        digest.update(clip.source_id.encode("utf-8"))
        digest.update(b"\0")
        digest.update(clip.source_sha256.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def prepare_aishell(budget: TransferBudget) -> tuple[list[Clip], dict[str, Any]]:
    speakers, entries, speaker_sha, content_sha, transcript_url = aishell_metadata(budget)
    parsed: list[tuple[str, str, str]] = []
    for filename, text in entries:
        match = re.match(r"^(SSB\d{4})(\d+)\.wav$", filename)
        if match and match.group(1) in speakers:
            parsed.append((match.group(1), match.group(2), text))
    if not parsed:
        raise PreparationError("AISHELL train transcript rows did not match official speaker metadata")
    selected_speaker = min(row[0] for row in parsed)
    speaker_meta = speakers[selected_speaker]
    speaker_rows = sorted((row for row in parsed if row[0] == selected_speaker), key=lambda row: int(row[1]))
    oids = aishell_file_oids(selected_speaker, budget)
    clips: list[Clip] = []
    source_seconds = 0.0
    for _, number, text in speaker_rows:
        filename = f"{selected_speaker}{number}.wav"
        relative = f"train/wav/{selected_speaker}/{filename}"
        cache_path = cached_clip_path("aishell3", AISHELL_REV, Path(filename).stem, ".wav")
        url = f"https://huggingface.co/datasets/{AISHELL_REPO}/resolve/{AISHELL_REV}/{relative}"
        raw = get_clip_bytes(cache_path, url, budget, label=f"AISHELL {filename}")
        clip = make_clip(
            language="zh", corpus="aishell3", source_id=Path(filename).stem,
            speaker_id=selected_speaker, chapter_id=None, text=text,
            source_path=relative, raw=raw, expected_git_blob_oid=oids.get(filename),
        )
        clips.append(clip)
        source_seconds += clip.duration_seconds
        if source_seconds >= TARGET_SPEECH_SECONDS:
            break
    if source_seconds < TARGET_SPEECH_SECONDS:
        raise PreparationError(f"AISHELL speaker {selected_speaker} yielded only {source_seconds:.3f} source seconds")
    metadata = {
        "dataset": AISHELL_REPO,
        "dataset_revision": AISHELL_REV,
        "split": "train",
        "speaker_id": selected_speaker,
        "speaker_metadata": {"age_group": speaker_meta[0], "gender": speaker_meta[1], "accent": speaker_meta[2]},
        "speaker_metadata_sha256": speaker_sha,
        "transcript_file": "train/content.txt",
        "transcript_url": transcript_url,
        "transcript_file_sha256": content_sha,
        "license": "Apache-2.0",
        "license_url": AISHELL_LICENSE,
        "source_url": AISHELL_CARD,
        "selection_rule": "First lexicographic speaker ID present in both train/content.txt and spk-info.txt; then numeric utterance suffix order, shortest prefix with at least 300 seconds of decoded source audio.",
        "transcript_rule": "The source content.txt alternates Chinese character and pinyin tokens; the fixed reference is the even-indexed character token sequence as published.",
        "utterance_count_in_catalog_for_speaker": len(speaker_rows),
    }
    return clips, metadata


def prepare_librispeech(budget: TransferBudget) -> tuple[list[Clip], dict[str, Any]]:
    catalog, catalog_sha = load_librispeech_catalog(budget)
    clean = [row for row in catalog if row.get("id") and row.get("speaker_id") is not None and row.get("chapter_id") is not None]
    if not clean:
        raise PreparationError("LibriSpeech catalog contains no valid rows")
    selected_speaker = min(int(row["speaker_id"]) for row in clean)
    speaker_rows = sorted(
        (row for row in clean if int(row["speaker_id"]) == selected_speaker),
        key=lambda row: int(row["row_idx"]),
    )
    source_urls = get_librispeech_viewer_urls(speaker_rows, budget)
    clips: list[Clip] = []
    source_seconds = 0.0
    for row in speaker_rows:
        source_id = row["id"]
        cache_path = cached_clip_path("librispeech", LIBRI_REV, source_id, ".audio")
        url = source_urls.get(source_id)
        raw = get_clip_bytes(cache_path, url, budget, label=f"LibriSpeech {source_id}")
        source_path = f"test-clean/{source_id.replace('-', '/')}.flac"
        clip = make_clip(
            language="en", corpus="librispeech", source_id=source_id,
            speaker_id=str(selected_speaker), chapter_id=str(row["chapter_id"]),
            text=row["text"], source_path=source_path, raw=raw,
        )
        clips.append(clip)
        source_seconds += clip.duration_seconds
        if source_seconds >= TARGET_SPEECH_SECONDS:
            break
    if source_seconds < TARGET_SPEECH_SECONDS:
        raise PreparationError(f"LibriSpeech speaker {selected_speaker} yielded only {source_seconds:.3f} source seconds")
    metadata = {
        "dataset": LIBRI_REPO,
        "dataset_revision": LIBRI_REV,
        "split": "test.clean",
        "speaker_id": selected_speaker,
        "chapter_ids": list(dict.fromkeys(clip.chapter_id for clip in clips)),
        "sanitized_catalog_sha256": catalog_sha,
        "license": "CC BY 4.0",
        "license_url": LIBRI_LICENSE,
        "source_url": LIBRI_CARD,
        "corpus_description_url": LIBRI_SOURCE,
        "selection_rule": "Lowest numeric speaker_id in test.clean; preserve dataset row order; shortest prefix with at least 300 seconds of decoded source audio. The row catalog is from the pinned repository revision.",
        "transcript_rule": "Use the corpus test.clean row text verbatim (uppercased as published); no ASR-generated reference text.",
    }
    return clips, metadata


def prepare_language(language: str, clips: list[Clip], metadata: dict[str, Any]) -> dict[str, Any]:
    reference_path = OUTPUT_DIR / f"{language}-single-speaker-reference.wav"
    fast_clips, units_per_second = select_minimal_window(clips, FAST_WINDOW_SECONDS)
    fast_path = OUTPUT_DIR / f"{language}-fast-speech-listening-candidate.wav"
    reference = write_asset(clips, reference_path)
    fast = write_asset(fast_clips, fast_path)
    fast["candidate_basis"] = {
        "transcript_units": "Han characters" if language == "zh" else "whitespace-delimited words",
        "transcript_units_per_source_audio_second": round(units_per_second, 6),
        "selection_rule": f"Among all earliest-start contiguous utterance windows reaching at least {FAST_WINDOW_SECONDS} source-audio seconds within the fixed reference prefix, select the highest published-transcript units/second; ties select the earliest start.",
        "manual_listening_status": "pending; this rate proxy does not establish that a human listener would classify the passage as fast speech.",
        "speed_change": "none; source recording playback speed is unchanged.",
    }
    fast["ordered_source_utterance_sha256"] = ordered_clip_digest(fast_clips)
    reference["ordered_source_utterance_sha256"] = ordered_clip_digest(clips)
    return {
        "source": metadata,
        "reference_asset": reference,
        "fast_speech_candidate_asset": fast,
    }


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    budget = TransferBudget()
    try:
        zh_clips, zh_source = prepare_aishell(budget)
        print(f"Prepared source selection: zh speaker={zh_source['speaker_id']} utterances={len(zh_clips)} source_seconds={sum(x.duration_seconds for x in zh_clips):.3f}")
        en_clips, en_source = prepare_librispeech(budget)
        print(f"Prepared source selection: en speaker={en_source['speaker_id']} utterances={len(en_clips)} source_seconds={sum(x.duration_seconds for x in en_clips):.3f}")
        manifest = {
            "schema_version": 1,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "script": "test/prepare_original_acceptance_media.py",
            "source_cache_root": str(CACHE_ROOT),
            "transfer_budget_bytes": TRANSFER_BUDGET_BYTES,
            "network_bytes_this_run": budget.transferred,
            "unique_source_audio_payload_bytes": sum(clip.source_bytes for clip in zh_clips + en_clips),
            "unique_source_audio_utterance_count": len(zh_clips) + len(en_clips),
            "target_source_audio_seconds_per_language": TARGET_SPEECH_SECONDS,
            "silence_between_source_utterances_seconds": SILENCE_BETWEEN_UTTERANCES_SECONDS,
            "output_conversion": "PyAV decode; resample to mono 16 kHz signed 16-bit PCM; preserve each full utterance; append exactly 0.25 seconds of digital silence between utterances; no leading/trailing silence, denoising, trimming, or speed modification.",
            "source_audio_duration_definition": "source_utterance_audio_seconds counts the complete decoded source utterance clips and therefore includes natural pauses already present inside each clip. No VAD or human speech-boundary measurement was applied; inserted inter-utterance silence is counted separately.",
            "manual_review": "No human listening or transcript-to-audio verification has yet been performed. All fast-speech assets are rate-ranked listening candidates only; they must not be reported as confirmed fast-speech evidence until manually reviewed.",
            "languages": {
                "zh": prepare_language("zh", zh_clips, zh_source),
                "en": prepare_language("en", en_clips, en_source),
            },
        }
        encoded = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        atomic_write(MANIFEST_PATH, encoded)
    except PreparationError as exc:
        print(f"Preparation failed: {exc}", file=sys.stderr)
        return 2

    print(f"Manifest: {MANIFEST_PATH.relative_to(REPO)}")
    print(f"Network transfer: {budget.transferred} bytes (limit {TRANSFER_BUDGET_BYTES})")
    for lang in ("zh", "en"):
        data = manifest["languages"][lang]
        for asset_key in ("reference_asset", "fast_speech_candidate_asset"):
            asset = data[asset_key]
            print(f"{lang} {asset_key}: {asset['file']} sha256={asset['sha256']} duration={asset['duration_seconds']}s source_audio={asset['source_utterance_audio_seconds']}s inserted_silence={asset['inserted_silence_seconds']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
