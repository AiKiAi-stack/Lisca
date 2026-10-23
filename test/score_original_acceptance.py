"""Score a fixed reference manifest against a recorded original-caption session.

Normalization is fixed before evaluation: NFKC, lowercase, remove punctuation,
retain written numeric forms. Chinese ignores whitespace; English uses words.
This measures transcript accuracy, not browser timing or caption visibility.
"""
import argparse
import json
from pathlib import Path
import re
import unicodedata


def tokens(text, language):
    text = unicodedata.normalize("NFKC", text).lower()
    text = "".join(c for c in text if c.isalnum() or c.isspace())
    return list("".join(text.split())) if language == "zh" else text.split()


def edit_distance(reference, hypothesis):
    previous = list(range(len(hypothesis) + 1))
    for index, expected in enumerate(reference, 1):
        current = [index]
        for column, actual in enumerate(hypothesis, 1):
            current.append(min(current[-1] + 1, previous[column] + 1,
                               previous[column - 1] + (expected != actual)))
        previous = current
    return previous[-1]


def valid_sha256(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value) is not None


def score(manifest, recording, language):
    active = {}
    for event in recording["events"]:
        sentence_id = event.get("sentence_id")
        identity = ("sentence", sentence_id) if sentence_id is not None else ("time", event.get("beg"))
        if event["type"] == "caption_remove":
            if sentence_id is None and event.get("beg") is None:
                # The server withdraws all anonymous captions without a start time.
                active = {key: value for key, value in active.items() if key[0] != "time"}
            else:
                active.pop(identity, None)
        elif event["type"] == "subtitle" and event.get("is_final"):
            active[identity] = event
    transcript = " ".join(e["text"] for e in sorted(active.values(), key=lambda e: e["beg"]))
    reference = " ".join(item["reference"] for item in manifest["items"])
    ref, hyp = tokens(reference, language), tokens(transcript, language)
    if not ref:
        raise ValueError("Reference set must not be empty")
    edits = edit_distance(ref, hyp)
    error_rate = edits / len(ref)
    events = recording["events"]
    transport_ended = any(event["type"] == "end" for event in events)
    no_errors = not recording.get("errors") and not any(event["type"] == "error" for event in events)
    if "recording_status" in recording:
        complete = (recording["recording_status"] == "completed"
                    and recording.get("completion_signal") == "end"
                    and transport_ended and no_errors)
    else:
        # Legacy recordings can still be scored for accuracy, but cannot prove provenance.
        complete = bool(active) and transport_ended and no_errors
    translated = max(recording.get("translated_cues", 0),
                     sum(bool(event.get("translated")) for event in events))
    reference_sha256 = manifest.get("sha256")
    input_sha256 = recording.get("input_sha256")
    valid_reference_hash = valid_sha256(reference_sha256)
    valid_input_hash = valid_sha256(input_sha256)
    input_matches_reference = (input_sha256.lower() == reference_sha256.lower()
                               if valid_input_hash and valid_reference_hash else None)
    if recording.get("source_kind") == "remote_stream":
        sample = recording.get("sample_interval") or {}
        source_status = ("sampled_remote" if recording.get("source_url")
                         and sample.get("started_at_utc") and sample.get("ended_at_utc")
                         else "missing_remote_sample")
    elif recording.get("source_kind") == "local_file":
        if not valid_input_hash or not valid_reference_hash:
            source_status = "missing_hash"
        elif (recording.get("input_unchanged") is not True
              or not valid_sha256(recording.get("input_sha256_after"))
              or input_sha256.lower() != recording["input_sha256_after"].lower()):
            source_status = "input_changed"
        elif input_matches_reference is False:
            source_status = "hash_mismatch"
        else:
            source_status = "verified"
    elif "source_kind" not in recording and "input_sha256" not in recording:
        source_status = "legacy_missing_identity"
    else:
        source_status = "missing_identity"
    source_verified = source_status == "verified"
    transcription_accuracy_passed = (complete and not translated
                                     and error_rate <= (.10 if language == "zh" else .15))
    return {"language": language, "metric": "CER" if language == "zh" else "WER",
            "dataset": manifest["dataset"], "config": manifest["config"],
            "reference_sha256": reference_sha256, "input_sha256": input_sha256,
            "input_matches_reference": input_matches_reference,
            "source_kind": recording.get("source_kind", "unknown"),
            "source_verified": bool(source_verified),
            "source_status": source_status,
            "source_url": recording.get("source_url"),
            "sample_interval": recording.get("sample_interval"),
            "recording_status": recording.get("recording_status", "legacy_unknown"),
            "completion_signal": recording.get("completion_signal"),
            "samples": len(manifest["items"]),
            "audio_seconds": manifest["seconds"], "normalization": __doc__.split("\n\n")[1].strip(),
            "reference_tokens": len(ref), "hypothesis_tokens": len(hyp), "edits": edits,
            "error_rate": error_rate, "session_without_errors": complete,
            "transcription_accuracy_passed": transcription_accuracy_passed,
            "translated_cues": translated,
            "accuracy_gate_passed": transcription_accuracy_passed,
            "sync_gate_passed": None, "sync_status": "not_assessed",
            "release_gate_passed": False,
            "transcript": transcript, "reference": reference}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("recording", type=Path)
    parser.add_argument("--language", choices=("zh", "en"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = score(json.loads(args.manifest.read_text(encoding="utf-8")),
                   json.loads(args.recording.read_text(encoding="utf-8")), args.language)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in ("reference", "transcript")}, ensure_ascii=False))
