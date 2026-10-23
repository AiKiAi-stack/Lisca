"""Score a fixed reference manifest against a recorded original-caption session.

Normalization is fixed before evaluation: NFKC, lowercase, remove punctuation,
retain written numeric forms. Chinese ignores whitespace; English uses words.
This measures transcript accuracy, not browser timing or caption visibility.
"""
import argparse
import json
from pathlib import Path
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
    complete = (bool(active) and any(event["type"] == "end" for event in events)
                and not recording.get("errors")
                and not any(event["type"] == "error" for event in events))
    translated = max(recording.get("translated_cues", 0),
                     sum(bool(event.get("translated")) for event in events))
    return {"language": language, "metric": "CER" if language == "zh" else "WER",
            "dataset": manifest["dataset"], "config": manifest["config"],
            "reference_sha256": manifest["sha256"], "samples": len(manifest["items"]),
            "audio_seconds": manifest["seconds"], "normalization": __doc__.split("\n\n")[1].strip(),
            "reference_tokens": len(ref), "hypothesis_tokens": len(hyp), "edits": edits,
            "error_rate": error_rate, "session_without_errors": complete,
            "translated_cues": translated,
            "accuracy_gate_passed": complete and not translated and error_rate <= (.10 if language == "zh" else .15),
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
