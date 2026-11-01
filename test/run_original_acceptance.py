"""Record explicitly requested original-language recognition; no credentials in artifacts."""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from urllib.request import Request, urlopen

import av
import websockets


URL_PATTERN = re.compile(r"(?i)\b(?:https?|wss?|rtmps?|ftps?|mms)://[^\s<>\"']+")
SECRET_FIELD_PATTERN = re.compile(
    r"(?i)([\"']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|"
    r"session(?:[_-]?(?:id|token))?|password|passwd|secret|credential|"
    r"authorization|cookie|signature|sig|auth)[\"']?\s*[:=]\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;}&]+)"
)
SENSITIVE_FIELD_NAMES = {
    "api_key", "access_token", "refresh_token", "token", "session", "session_id",
    "session_token", "password", "passwd", "secret", "credential", "authorization",
    "cookie", "signature", "sig", "auth",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_identity(path):
    stat = Path(path).stat()
    return {"device": stat.st_dev, "inode": stat.st_ino, "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns}


def redact_remote_source(source):
    return redact_text(source)


def _is_sensitive_field(name):
    normalized = str(name).lower().replace("-", "_")
    return (normalized in SENSITIVE_FIELD_NAMES
            or normalized.endswith(("_api_key", "_token", "_secret", "_password",
                                    "_credential", "_signature", "_cookie", "_authorization",
                                    "_sig", "_auth")))


def _redact_url(url):
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        return "[redacted-url]"
    if not parts.scheme or not parts.netloc or not hostname:
        return "[redacted-url]"
    host = f"[{hostname}]" if ":" in hostname else hostname
    if port:
        host = f"{host}:{port}"
    return urlunsplit((parts.scheme, host, parts.path,
                       "[redacted]" if parts.query else "", ""))


def redact_text(value, secrets=()):
    def redact_url_match(match):
        candidate = match.group(0)
        suffix = ""
        while candidate and candidate[-1] in ".,;:!?)}":
            suffix = candidate[-1] + suffix
            candidate = candidate[:-1]
        return _redact_url(candidate) + suffix

    text = URL_PATTERN.sub(redact_url_match, str(value))
    text = SECRET_FIELD_PATTERN.sub(r"\1[redacted]", text)
    for secret in secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, "[redacted]")
    return text


def _url_secret_values(url):
    try:
        parts = urlsplit(url)
        values = {parts.username, parts.password}
        values.update(value for _key, value in parse_qsl(parts.query, keep_blank_values=True))
    except (TypeError, ValueError):
        return set()
    return {value for value in values if value}


def _collect_sensitive_values(value, secrets):
    if isinstance(value, dict):
        for key, item in value.items():
            if _is_sensitive_field(key) and isinstance(item, str):
                secrets.add(item)
            _collect_sensitive_values(item, secrets)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect_sensitive_values(item, secrets)
    elif isinstance(value, str):
        for match in URL_PATTERN.finditer(value):
            secrets.update(_url_secret_values(match.group(0).rstrip(".,;:!?)}")))


def redact_recording(value, secrets=()):
    secrets = set(secrets)
    _collect_sensitive_values(value, secrets)

    def redact(item, key=None):
        if isinstance(item, dict):
            return {redact_text(name, secrets): ("[redacted]" if _is_sensitive_field(name)
                                                 else redact(child, name))
                    for name, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [redact(child) for child in item]
        if isinstance(item, str):
            return "[redacted]" if key is not None and _is_sensitive_field(key) else redact_text(item, secrets)
        return item

    return redact(value)


def write_recording(path, output_path, events, status, completion_signal, completion_reason, errors,
                    started_at, started, metadata=None, source_before=None, secrets=()):
    is_remote = path.startswith(("http://", "https://"))
    source_kind = "remote_stream" if is_remote else "local_file"
    finished_at = utc_now()
    input_hash = None
    input_hash_after = None
    input_unchanged = False
    identity_after = None
    if not is_remote:
        input_hash = source_before["sha256"] if source_before else None
        try:
            identity_after = file_identity(path)
            input_hash_after = sha256_file(path)
        except OSError as exc:
            errors.append({"type": "source_read_error", "message": str(exc)})
            status = "failed"
        input_unchanged = bool(
            source_before
            and identity_after == source_before["identity"]
            and input_hash_after == input_hash
        )
    remote_source = redact_remote_source(path) if is_remote else None
    elapsed = round(time.monotonic() - started, 3)
    result = {
        "input": remote_source if is_remote else str(Path(path).resolve()),
        "source_kind": source_kind,
        "input_sha256": input_hash,
        "input_sha256_after": input_hash_after,
        "input_identity_before": source_before["identity"] if source_before else None,
        "input_identity_after": identity_after,
        "input_unchanged": input_unchanged if not is_remote else None,
        "source_url": remote_source,
        "sample_interval": {"started_at_utc": started_at, "ended_at_utc": finished_at,
                            "elapsed_seconds": elapsed},
        "recording_status": status,
        "completion_signal": completion_signal,
        "completion_reason": completion_reason,
        "mode": "transcribe",
        "metadata": metadata,
        "events": events,
        "errors": errors,
        "final_cues": [e for e in events if e.get("type") == "subtitle" and e.get("is_final")],
        "translated_cues": sum(bool(e.get("translated")) for e in events),
        "completed_after_ms": round(elapsed * 1000) if status == "completed" else None,
    }
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = redact_recording(result, secrets)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


async def run(path, label, output_path, server_url):
    root = Path(__file__).resolve().parents[1]
    directory = root / "logs" / "original-caption-acceptance"
    output_path = output_path or directory / (label + ".json")
    server_url = server_url.rstrip("/")
    websocket_url = server_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    is_remote = path.startswith(("http://", "https://"))
    input_path = path if is_remote else str(Path(path).resolve())
    source_before = None
    metadata = None
    payload = {"url": input_path, "mode": "transcribe", "source": "auto"}
    started_at = utc_now()
    started = time.monotonic()
    events = []
    errors = []
    status = "failed"
    completion_signal = None
    completion_reason = None
    ticket = None
    secrets = _url_secret_values(path) | _url_secret_values(server_url)
    try:
        if not is_remote:
            source_before = {"identity": file_identity(input_path), "sha256": sha256_file(input_path)}
        with av.open(input_path) as media:
            metadata = {"duration": media.duration / av.time_base if media.duration else None,
                        "audio": [{"codec": s.codec_context.name, "rate": s.codec_context.sample_rate,
                                   "start_time": float(s.start_time * s.time_base)
                                   if s.start_time is not None else None}
                                  for s in media.streams.audio]}
        request = Request(server_url + "/api/cloud/sessions", data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=10) as response:
            ticket = json.load(response)
        if ticket.get("session"):
            secrets.add(ticket["session"])
        async with websockets.connect(websocket_url + "/ws?session=" + ticket["session"],
                                      max_size=2**23, close_timeout=1) as socket:
            while True:
                message = await asyncio.wait_for(socket.recv(), timeout=90)
                event = json.loads(message)
                events.append({**event, "observed_after_ms": round((time.monotonic()-started)*1000)})
                if event.get("type") == "error":
                    errors.append(event)
                if event.get("type") == "end":
                    completion_signal = "end"
                    reason = event.get("reason")
                    completion_reason = (reason if reason in ("completed", "cancelled", "error")
                                         else "completion_unverified")
                    status = {"completed": "completed", "cancelled": "cancelled",
                              "error": "failed", "completion_unverified": "completion_unverified"}[
                                  completion_reason]
                    break
    except asyncio.CancelledError:
        status, completion_signal, completion_reason = "cancelled", "cancelled", "cancelled"
        errors.append({"type": "cancelled", "message": "Recording cancelled by operator"})
    except KeyboardInterrupt:
        status, completion_signal, completion_reason = "cancelled", "cancelled", "cancelled"
        errors.append({"type": "cancelled", "message": "Recording cancelled by operator"})
    except Exception as exc:
        status = "failed"
        errors.append({"type": "recording_error", "message": str(exc)})
    finally:
        if ticket and ticket.get("session"):
            try:
                with urlopen(Request(server_url + "/api/cloud/sessions/" + ticket["session"],
                                     method="DELETE"), timeout=20):
                    pass
            except Exception as exc:
                errors.append({"type": "cleanup_error", "message": str(exc)})
                status = "failed"
        if errors and status == "completed":
            status = "failed"
        result = write_recording(input_path, output_path, events, status, completion_signal,
                                 completion_reason, errors, started_at, started, metadata,
                                 source_before, secrets)
    print(json.dumps({k: v for k, v in result.items() if k not in ("events", "final_cues", "input")},
                     ensure_ascii=False), flush=True)
    if (status != "completed" or not result["final_cues"] or result["translated_cues"]
            or result["input_unchanged"] is False):
        raise SystemExit("Original-language recognition did not pass")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Local media path or remote stream URL")
    parser.add_argument("--label", default="user-video")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--server-url", default="http://127.0.0.1:8002",
                        help="Recognition session service base URL")
    args = parser.parse_args()
    media_input = args.path if args.path.startswith(("http://", "https://")) else str(Path(args.path).resolve())
    asyncio.run(run(media_input, args.label, args.output, args.server_url))
