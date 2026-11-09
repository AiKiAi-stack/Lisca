"""Launch the WebUI with bounded source capture for a paired acceptance run.

For a controlled real run, start ``tests/acceptance/run_real_browser_acceptance.py`` with
``--stop-file`` set to the same path as ``--invalid-stop-file``. The browser
collector watches that marker and stops its active session; this launcher does
not monitor the marker or stop recognition by itself.
"""
import argparse
from pathlib import Path
import sys

from long_acceptance_probe import ProbeFinalizationError, install_probe


def _arguments(argv):
    parser = argparse.ArgumentParser(allow_abbrev=False, description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-pcm-bytes", required=True, type=int)
    parser.add_argument(
        "--invalid-stop-file", required=True,
        help=("Use the same path for the paired browser collector's --stop-file; "
              "that collector stops the active session when this marker appears."),
    )
    return parser.parse_args(argv)


def main(argv=None, *, webui_module=None, sdk_recognition_class=None,
         sdk_callback_class=None):
    args = _arguments(argv)
    if webui_module is None:
        root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(root))
        import lisca.web.app as webui_module  # pylint: disable=import-error
    if sdk_recognition_class is None:
        from dashscope.audio.asr import RecognitionCallback
        from lisca.asr.asr_client import Recognition
        sdk_recognition_class, sdk_callback_class = Recognition, RecognitionCallback

    installation = install_probe(
        webui_module, args.manifest, args.output,
        max_pcm_bytes=args.max_pcm_bytes, invalid_stop_file=args.invalid_stop_file,
        sdk_recognition_class=sdk_recognition_class,
        sdk_callback_class=sdk_callback_class,
    )
    original_argv = sys.argv
    receipt_error = False
    try:
        # Bind the regular WebUI locally on its normal port; the runner does not start ASR.
        sys.argv = [original_argv[0], "--host", "127.0.0.1", "--port", "8002"]
        webui_module.main()
    finally:
        try:
            installation.restore()
        except ProbeFinalizationError:
            print("long acceptance probe could not write run-closed receipt", file=sys.stderr)
            receipt_error = True
        finally:
            sys.argv = original_argv
    if receipt_error:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
