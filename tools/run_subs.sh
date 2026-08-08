#!/bin/bash
# 拉流字幕启动器：注入 CUDA 运行库路径后启动 stream_subs.py
set -e
cd "$(dirname "$0")/.."
export LD_LIBRARY_PATH="$PWD/.venv/lib/python3.10/site-packages/nvidia/cublas/lib:$PWD/.venv/lib/python3.10/site-packages/nvidia/cudnn/lib:$LD_LIBRARY_PATH"
exec .venv/bin/python tools/stream_subs.py "$@"
