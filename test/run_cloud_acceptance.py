"""Record a short, explicitly authorized real-cloud run via local HTTP/WebSocket.

Uses the Key already configured in the local UI; never reads or writes the Key.
"""
import argparse
import asyncio
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen

import websockets

DIRECTORY = Path(__file__).resolve().parents[1] / "logs" / "translation-acceptance"


async def run(source, media_url=None, seconds=None, label=None):
    target = "en" if source == "zh" else "zh"
    payload = {"mode": "translate", "url": media_url or str(DIRECTORY / f"{source}.mp4"), "source": source, "target": target}
    request = Request("http://127.0.0.1:8002/api/cloud/sessions",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=10) as response:
        ticket = json.load(response)
    events = []
    started = time.monotonic()
    async with websockets.connect("ws://127.0.0.1:8002/ws?session=" + ticket["session"],
                                  max_size=2**20) as socket:
        while True:
            remaining = seconds - (time.monotonic() - started) if seconds else 45
            if remaining <= 0:
                break
            try:
                message = await asyncio.wait_for(socket.recv(), timeout=min(45, remaining))
            except asyncio.TimeoutError:
                if seconds:
                    break
                raise
            event = json.loads(message)
            events.append({**event, "observed_after_ms": round((time.monotonic() - started) * 1000)})
            if event["type"] == "end":
                break
        with urlopen(Request("http://127.0.0.1:8002/api/cloud/sessions/" + ticket["session"],
                             method="DELETE"), timeout=20):
            pass
    cues = [event for event in events if event["type"] == "subtitle"]
    finals = [event for event in cues if event["is_final"]]
    result = {"source": source, "target": target, "input": payload["url"],
              "events": events, "final_cues": finals,
              "first_translation_ms": cues[0]["observed_after_ms"] if cues else None,
              "errors": [event for event in events if event["type"] == "error"]}
    path = DIRECTORY / f"{label or (source + '-to-' + target)}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "events"},
                     ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("zh", "en"), required=True)
    parser.add_argument("--url")
    parser.add_argument("--seconds", type=float)
    parser.add_argument("--label")
    arguments = parser.parse_args()
    asyncio.run(run(arguments.source, arguments.url, arguments.seconds, arguments.label))
