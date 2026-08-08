"""ticket-02: 实时字幕主程序

拉流（StreamSource）→ ASR（whisper_streaming OnlineASRProcessor）→ 实时句子级字幕。

用法:
    python stream_subs.py <本地文件或流URL> [--model_dir models/faster-whisper-small]

注意: 需要 CUDA 运行库在 LD_LIBRARY_PATH（GPU 推理），建议用 run_subs.sh 启动。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "whisper_streaming"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from whisper_online import FasterWhisperASR, OnlineASRProcessor  # noqa: E402
from stream_source import StreamSource, s16_to_f32  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="拉流实时字幕")
    ap.add_argument("input", help="本地文件路径或视频流/音频流 URL")
    ap.add_argument("--model_dir", default="models/faster-whisper-small", help="faster-whisper 模型目录")
    ap.add_argument("--language", default="zh", help="源语言（zh/en/auto...）")
    ap.add_argument("--min-chunk-size", type=float, default=1.0, help="最小处理块（秒）")
    ap.add_argument("--chunk-sec", type=float, default=0.1, help="拉流分块大小（秒）")
    args = ap.parse_args()

    asr = FasterWhisperASR(lan=args.language, model_dir=args.model_dir)
    online = OnlineASRProcessor(asr)

    src = StreamSource(args.input, chunk_sec=args.chunk_sec)
    try:
        for chunk in src.iter_chunks():
            audio = s16_to_f32(chunk)
            online.insert_audio_chunk(audio)
            o = online.process_iter()
            if o[0] is not None:
                print(f"[{o[0]*1000:7.0f}-{o[1]*1000:7.0f}ms] {o[2]}", flush=True)
        o = online.finish()
        if o[0] is not None:
            print(f"[{o[0]*1000:7.0f}-{o[1]*1000:7.0f}ms] {o[2]}", flush=True)
    finally:
        src.close()


if __name__ == "__main__":
    main()
