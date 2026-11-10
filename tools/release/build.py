"""Build an archive of the tagged cloud WebUI with an embedded Python runtime."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    tooling = Path(__file__).resolve().parents[2]
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lisca-build-") as temporary:
        work = Path(temporary)
        command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                   "--name", "Lisca", "--onedir", "--noupx",
                   "--distpath", str(work / "dist"), "--workpath", str(work / "work"),
                   "--specpath", str(work), "--paths", str(source),
                   "--add-data", f"{source / 'lisca/web/static'}:lisca/web/static",
                   "--add-data", f"{source / 'lisca/web/licenses'}:lisca/web/licenses",
                   "--add-data", f"{source / 'lisca/_vendor/whisper_streaming/LICENSE'}:lisca/_vendor/whisper_streaming",
                   "--hidden-import", "lisca.engines.aliyun_funasr",
                   "--collect-submodules", "uvicorn",
                   "--collect-all", "av", "--collect-all", "dashscope",
                   "--collect-all", "yt_dlp", str(Path(__file__).with_name("entry.py"))]
        subprocess.run(command, cwd=source, check=True)
        bundle = work / "dist/Lisca"
        suffix = ".exe" if sys.platform == "win32" else ""
        # Both commands share the embedded runtime; the entry point dispatches by name.
        shutil.copy2(bundle / ("Lisca" + suffix), bundle / ("yt-dlp" + suffix))
        shutil.copy2(source / "LICENSE", bundle / "LICENSE")
        licenses = bundle / "licenses"
        for distribution in importlib.metadata.distributions():
            for relative in distribution.files or []:
                if relative.name.lower().startswith(("license", "copying", "notice")):
                    original = Path(distribution.locate_file(relative))
                    if original.is_file():
                        target = licenses / distribution.metadata["Name"] / str(relative).replace("..", "_")
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(original, target)
        def commit(directory):
            return subprocess.check_output(["git", "-C", str(directory), "rev-parse", "HEAD"], text=True).strip()
        (bundle / "build-info.json").write_text(json.dumps({
            "source_commit": commit(source), "packaging_commit": commit(tooling),
            "platform": platform.platform(), "python": platform.python_version(),
            "dependencies": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()},
        }, indent=2), encoding="utf-8")
        (bundle / "README.txt").write_text(
            "Lisca\n\n"
            "Windows: double-click Lisca.exe. Linux: run ./Lisca.\n"
            "Open http://127.0.0.1:8002/ and configure your DashScope API key.\n"
            "Keep this folder together; close the terminal to stop the server.\n"
            "Settings and uploads are stored in data/ next to the executable.\n"
            "Cloud recognition sends audio to DashScope and incurs API charges.\n"
            "Use --check for offline diagnostics, or --help for server options.\n",
            encoding="utf-8")
        # Read the tag's version, not the packaging checkout's installed metadata.
        version = re.search(r'^version = "([^"]+)"',
                            (source / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)
        name = f"Lisca-{version}-{'windows' if suffix else 'linux'}-x64"
        archive = shutil.make_archive(str(output / name), "zip" if suffix else "gztar",
                                      root_dir=bundle.parent, base_dir=bundle.name)
        print(archive)


if __name__ == "__main__":
    main()
