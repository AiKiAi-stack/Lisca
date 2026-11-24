"""Read local operation records without starting the WebUI (#114).

Examples:
    python -m lisca.ops --session <id>
    python -m lisca.ops --kind user --kind system --limit 50
    python -m lisca.ops --full > ops-full.jsonl
"""
import argparse
import json
import sys

from lisca.paths import DATA_DIR
from lisca.web.operation_log import OpsLog, redact_entry


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lisca.ops", description="读取本机操作记录（JSONL）")
    parser.add_argument("--session", default="", help="只看某个会话 ID")
    parser.add_argument("--kind", action="append", default=[],
                        help="按类型过滤：user/http/session/system，可重复")
    parser.add_argument("--limit", type=int, default=200,
                        help="最多输出最近 N 条（默认 200）")
    parser.add_argument("--full", action="store_true",
                        help="输出完整 URL（默认脱敏）；仅在本机使用")
    parser.add_argument("--dir", default=str(DATA_DIR / "logs"),
                        help=argparse.SUPPRESS)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    ops = OpsLog(args.dir)
    events = ops.read(session=args.session or None, limit=10 ** 9)
    if args.kind:
        events = [event for event in events if event.get("kind") in set(args.kind)]
    events = events[-max(1, args.limit):]
    if not args.full:
        events = [redact_entry(event) for event in events]
    for event in events:
        print(json.dumps(event, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
