"""ticket-02/04: 实时字幕主程序（引擎抽象层版本）

拉流（StreamSource）→ 引擎（Engine，可配置切换）→ 实时句子级字幕。

用法:
    python stream_subs.py <本地文件或流URL> [--engine faster-whisper] [--model_dir ...]

注意: 本地引擎需要 CUDA 运行库在 LD_LIBRARY_PATH（GPU 推理），建议用 run_subs.sh 启动。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "whisper_streaming"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engines import create_engine  # noqa: E402
from input_resolver import resolve_stream_url  # noqa: E402
from stream_source import StreamSource, s16_to_f32  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="拉流实时字幕")
    ap.add_argument("input", help="本地文件路径或视频流/音频流 URL")
    ap.add_argument("--engine", default="faster-whisper",
                    help="ASR 引擎: faster-whisper（本地）/ aliyun-funasr（API）")
    ap.add_argument("--model_dir", default="models/faster-whisper-small", help="faster-whisper 模型目录")
    ap.add_argument("--language", default="zh", help="源语言（zh/en/auto...）")
    ap.add_argument("--chunk-sec", type=float, default=0.1, help="拉流分块大小（秒）")
    ap.add_argument("--srt", default="", help="SRT 字幕输出目录（可选，默认不写文件）")
    args = ap.parse_args()

    srt_writer = None
    if args.srt:
        # streamparser 的 logger.py 在 import 时用 dictConfig 把 root 配成 INFO+stdout，
        # 会污染字幕输出流，这里压回 WARNING
        import logging

        from streamparser.srt_writer import SRTWriter
        from streamparser.subtitle import SubtitleEntry

        srt_writer = SRTWriter(base_dir=args.srt)
        srt_writer.open(args.input)
        logging.getLogger().setLevel(logging.WARNING)

    def emit(o) -> None:
        if o[0] is None:
            return
        print(f"[{o[0]*1000:7.0f}-{o[1]*1000:7.0f}ms] {o[2]}", flush=True)
        if srt_writer is not None:
            srt_writer.write_entry(SubtitleEntry(
                index=0, text=o[2], start_time=o[0], end_time=o[1], is_final=True))

    engine = create_engine(args.engine, language=args.language, model_dir=args.model_dir)
    try:
        # 输入统一走 input_resolver（本地文件 / 流地址 / 平台页面 yt-dlp 解析）
        real_url = resolve_stream_url(args.input)
        src = StreamSource(real_url, chunk_sec=args.chunk_sec)
        try:
            for chunk in src.iter_chunks():
                emit(engine.transcribe_chunk(s16_to_f32(chunk)))
            emit(engine.finish())
        finally:
            src.close()
    finally:
        engine.close()
        if srt_writer is not None:
            srt_writer.close()


if __name__ == "__main__":
    main()
