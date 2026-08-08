"""ticket-03: 输入自动识别

自动判断输入类型并路由到可拉流的 URL：
- 本地文件路径（存在）→ 原样返回（StreamSource 直接读）
- 平台页面链接（bilibili/douyin/xiaohongshu）→ yt-dlp 解析真实流地址
- 其他 http(s)/rtsp 流地址 → 原样返回
"""
import os
import shutil
import subprocess
from typing import Tuple

# 平台域名映射（与 homeshare-worker 的 PLATFORM_HOSTS 同规则）
PLATFORM_HOSTS = {
    "douyin": ("douyin.com", "iesdouyin.com"),
    "xiaohongshu": ("xiaohongshu.com", "xhslink.com", "xhslink.cn"),
    "bilibili": ("bilibili.com", "b23.tv"),
}

InputKind = str  # 'file' | 'stream' | 'page'


def classify(user_input: str) -> Tuple[InputKind, str]:
    """返回 (kind, target)。kind: file / stream / page"""
    if os.path.exists(user_input):
        return ("file", user_input)
    if user_input.startswith(("http://", "https://", "rtsp://", "rtmp://", "rtp://")):
        host = user_input.lower()
        for platform, suffixes in PLATFORM_HOSTS.items():
            if any(s in host for s in suffixes):
                return ("page", user_input)
        return ("stream", user_input)
    raise ValueError(f"无法识别的输入（不是存在的文件，也不是 URL）: {user_input[:80]}")


def resolve_stream_url(user_input: str) -> str:
    """返回可直接拉流的 URL（页面链接经 yt-dlp 解析出第一个媒体流地址）"""
    kind, target = classify(user_input)
    if kind != "page":
        return target
    if shutil.which("yt-dlp") is None:
        raise RuntimeError("页面链接需要 yt-dlp，但未安装（pip install yt-dlp）")
    proc = subprocess.run(
        ["yt-dlp", "-g", "--no-warnings", "-f", "bestaudio/best", target],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp 解析失败: {proc.stderr.strip()[:200]}")
    urls = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not urls:
        raise RuntimeError(f"yt-dlp 未返回流地址: {target[:80]}")
    return urls[0]
