from __future__ import annotations

import argparse
import json
import sys

from .audio_analysis import analyze_audio
from .core import create_gradio_interface


def _cmd_analyze(args: argparse.Namespace) -> int:
    data = analyze_audio(
        args.audio_path,
        use_cache=not args.no_cache,
        cache_dir=args.cache_dir,
        mono=args.mono,
        target_sr=args.sr,
        n_mfcc=args.mfcc,
    )
    json.dump(data, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def _cmd_gradio(args: argparse.Namespace) -> int:
    demo = create_gradio_interface()
    demo.launch(server_name=args.host, server_port=args.port, share=args.share, inbrowser=args.inbrowser)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="deforum_music")
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("analyze", help="Analyze an audio file and print JSON")
    pa.add_argument("audio_path")
    pa.add_argument("--no-cache", action="store_true")
    pa.add_argument("--cache-dir", default=None)
    pa.add_argument("--mono", action="store_true", default=True)
    pa.add_argument("--sr", type=int, default=None)
    pa.add_argument("--mfcc", type=int, default=20)
    pa.set_defaults(func=_cmd_analyze)

    pg = sub.add_parser("gradio", help="Launch analyzer Gradio UI")
    pg.add_argument("--host", default="127.0.0.1")
    pg.add_argument("--port", type=int, default=7860)
    pg.add_argument("--share", action="store_true", default=False)
    pg.add_argument("--inbrowser", action="store_true", default=True)
    pg.set_defaults(func=_cmd_gradio)

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
