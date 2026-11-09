"""Opt-in local-live-media source; ordinary WebUI and real ASR stay in use.

Run with the WebUI's Python environment and one or two --source arguments
pointing to real videos with audio; optional --label arguments name each channel.
Load the printed channel-list URL in the normal WebUI, then start ASR there.
This helper does not create captions or start recognition. Each source
plays once unless --loop is explicit. Ctrl+C stops the encoder and removes its
temporary media. Use only after stopping the existing WebUI on port 8002.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from starlette.responses import Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[2]
MEDIA_URL = "http://127.0.0.1:8002/local-live/index.m3u8"
CHANNELS_PATH = "/local-live-channels.m3u"


class _LiveFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store"
        return response


def _command(ffmpeg, source, *, copy_media=False, loop=False):
    command = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "warning", "-n", "-re"]
    if loop:
        command += ["-stream_loop", "-1"]
    command += ["-i", str(source), "-map", "0:v:0", "-map", "0:a:0", "-sn", "-dn"]
    if copy_media:
        command += ["-c", "copy"]
    else:
        command += ["-c:v", "libx264", "-threads", "2", "-preset", "veryfast",
                    "-pix_fmt", "yuv420p", "-sc_threshold", "0",
                    "-force_key_frames", "expr:gte(t,n_forced*2)",
                    "-c:a", "aac", "-b:a", "128k"]
    return command + [
        "-f", "hls", "-hls_time", "2", "-hls_list_size", "8",
        "-hls_delete_threshold", "2", "-hls_flags",
        "delete_segments+omit_endlist+independent_segments+temp_file",
        "-hls_segment_filename", "segment-%09d.ts", "index.m3u8",
    ]


def _wait_ready(process, directory):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"local-live-media encoder exited: {process.returncode}")
        try:
            lines = (directory / "index.m3u8").read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            lines = []
        if sum(bool(line) and not line.startswith("#") for line in lines) >= 3:
            return
        time.sleep(0.1)
    raise RuntimeError("local-live-media did not publish three segments within 30 seconds")


@contextmanager
def replay_media(source, work_dir, ffmpeg, *, copy_media=False, loop=False):
    """Own a fresh directory and one direct encoder process; never edit source."""
    work_dir = Path(work_dir).resolve()
    if work_dir == ROOT or ROOT in work_dir.parents:
        raise ValueError("local-live-media work directory must be outside the checkout")
    work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="local-live-media-", dir=work_dir) as temporary:
        directory = Path(temporary)
        with (directory / "ffmpeg.log").open("w+b") as log:
            process = subprocess.Popen(
                _command(ffmpeg, source, copy_media=copy_media, loop=loop),
                cwd=directory, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=log, start_new_session=True,
            )
            try:
                try:
                    _wait_ready(process, directory)
                except RuntimeError as error:
                    log.flush()
                    log.seek(max(0, log.seek(0, os.SEEK_END) - 4096))
                    detail = log.read().decode("utf-8", errors="replace").strip()
                    raise RuntimeError(f"{error}\n{detail}") from error
                yield directory
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


def main(argv=None, *, webui_module=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--source", required=True, type=Path, action="append",
                        help="Real video with audio; repeat for at most two channels.")
    parser.add_argument("--label", action="append", help="Channel name; repeat once per source.")
    parser.add_argument("--ffmpeg", default=os.environ.get("FFMPEG_BINARY", "ffmpeg"))
    parser.add_argument("--work-dir", type=Path,
                        default=Path.home() / ".local/share/stream-with-asr/local-live")
    parser.add_argument("--loop", action="store_true", help="Explicitly repeat the source at EOF.")
    parser.add_argument("--copy-media", action="store_true",
                        help="Remux only; requires H.264/AAC and keyframes at most 2s apart.")
    args = parser.parse_args(argv)
    if len(args.source) > 2:
        parser.error("at most two --source arguments are allowed")
    sources = [source.expanduser().resolve() for source in args.source]
    if any(not source.is_file() for source in sources):
        parser.error("--source must be an existing local video with audio")
    if args.label is not None and len(args.label) != len(sources):
        parser.error("provide one --label for each --source")
    labels = args.label if args.label is not None else [source.stem for source in sources]
    if any(not label.strip() or "\n" in label or "\r" in label for label in labels):
        parser.error("channel labels must be nonempty single lines")
    ffmpeg = shutil.which(args.ffmpeg)
    if not ffmpeg:
        parser.error("ffmpeg not found; set --ffmpeg or FFMPEG_BINARY")
    if webui_module is None:
        sys.path.insert(0, str(ROOT))
        import lisca.web.app as webui_module  # pylint: disable=import-error
    mount_paths = ["/local-live", "/local-live-2"][:len(sources)]
    if any(getattr(route, "path", None) in [CHANNELS_PATH, *mount_paths]
           for route in webui_module.app.router.routes):
        parser.error("a local-live route is already registered")
    urls = [f"http://127.0.0.1:8002{path}/index.m3u8" for path in mount_paths]
    playlist = "#EXTM3U\n" + "".join(
        f"#EXTINF:-1,{label}\n{url}\n" for label, url in zip(labels, urls))

    async def channels(_request):
        return Response(playlist, media_type="audio/x-mpegurl",
                        headers={"Cache-Control": "no-store"})

    # shortcut: EOF leaves WebUI open; use --loop for runs longer than the source.
    with ExitStack() as stack:
        directories = [stack.enter_context(replay_media(
            source, args.work_dir.expanduser(), ffmpeg,
            copy_media=args.copy_media, loop=args.loop)) for source in sources]
        routes = [Route(CHANNELS_PATH, channels, name="local-live-channels")]
        routes += [Mount(path, app=_LiveFiles(directory=directory), name=f"local-live-media-{index}")
                   for index, (path, directory) in enumerate(zip(mount_paths, directories), 1)]
        webui_module.app.router.routes.extend(routes)
        original_argv = sys.argv
        try:
            print(f"Channel list: http://127.0.0.1:8002{CHANNELS_PATH}", flush=True)
            for label, url, directory in zip(labels, urls, directories):
                print(f"local-live-media (real ASR via normal WebUI), {label}: {url}", flush=True)
                print(f"Temporary media: {directory}", flush=True)
            sys.argv = [original_argv[0], "--host", "127.0.0.1", "--port", "8002"]
            webui_module.main()
        finally:
            sys.argv = original_argv
            for route in routes:
                webui_module.app.router.routes.remove(route)
    return 0


if __name__ == "__main__":
    # Uvicorn handles termination after startup; also clean up during HLS warmup.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    raise SystemExit(main())
