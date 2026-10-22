"""Record explicitly requested original-language recognition; no credentials in artifacts."""
import argparse
import asyncio
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen

import av
import websockets


async def run(path, label):
    root = Path(__file__).resolve().parents[1]
    directory = root / "logs" / "original-caption-acceptance"
    directory.mkdir(parents=True, exist_ok=True)
    with av.open(path) as media:
        metadata = {"duration": media.duration / av.time_base if media.duration else None,
            "audio": [{"codec": s.codec_context.name, "rate": s.codec_context.sample_rate,
                       "start_time": float(s.start_time * s.time_base) if s.start_time is not None else None}
                      for s in media.streams.audio]}
    payload = {"url": path, "mode": "transcribe", "source": "auto"}
    request = Request("http://127.0.0.1:8002/api/cloud/sessions", data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=10) as response:
        ticket = json.load(response)
    started = time.monotonic()
    events = []
    try:
        async with websockets.connect("ws://127.0.0.1:8002/ws?session=" + ticket["session"],
                                      max_size=2**23) as socket:
            while True:
                message = await asyncio.wait_for(socket.recv(), timeout=90)
                event = json.loads(message)
                events.append({**event, "observed_after_ms": round((time.monotonic()-started)*1000)})
                if event["type"] == "end":
                    break
    finally:
        with urlopen(Request("http://127.0.0.1:8002/api/cloud/sessions/" + ticket["session"],
                             method="DELETE"), timeout=20):
            pass
    finals = [e for e in events if e["type"] == "subtitle" and e.get("is_final")]
    errors = [e for e in events if e["type"] == "error"]
    result = {"input": path, "mode": "transcribe", "metadata": metadata, "events": events,
              "final_cues": finals, "errors": errors,
              "translated_cues": sum(bool(e.get("translated")) for e in events),
              "completed_after_ms": round((time.monotonic()-started)*1000)}
    (directory / (label + ".json")).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k != "events"},ensure_ascii=False),flush=True)
    if errors or not finals or result["translated_cues"]:
        raise SystemExit("Original-language recognition did not pass")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    parser.add_argument("--label", default="user-video")
    args = parser.parse_args()
    asyncio.run(run(str(Path(args.path).resolve()), args.label))
