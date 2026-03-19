"""Package entrypoint for the vendored EDMG engine.

Supported commands:
  python -m enhanced_deforum_music_generator selfcheck
  python -m enhanced_deforum_music_generator ui [--host 127.0.0.1] [--port 7860]
  python -m enhanced_deforum_music_generator api [--host 127.0.0.1] [--port 8000]
"""

from __future__ import annotations

import argparse
import sys


def _run_ui(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog='enhanced_deforum_music_generator ui')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=7860)
    parser.add_argument('--share', action='store_true')
    parser.add_argument('--inbrowser', action='store_true')
    args = parser.parse_args(argv)
    from .interface.gradio_interface import create_interface
    app = create_interface()
    app.launch(server_name=args.host, server_port=args.port, share=args.share, inbrowser=args.inbrowser)
    return 0


def _run_api(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog='enhanced_deforum_music_generator api')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args(argv)
    import uvicorn
    uvicorn.run('enhanced_deforum_music_generator.api.main:app', host=args.host, port=args.port, log_level='info')
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else 'selfcheck'
    rest = argv[1:] if argv else []
    if cmd in {'selfcheck', 'check'}:
        from .cli.selfcheck import run
        return int(run())
    if cmd in {'ui', 'gradio'}:
        return _run_ui(rest)
    if cmd in {'api', 'serve-api'}:
        return _run_api(rest)

    print(f'Unknown command: {cmd}', file=sys.stderr)
    print('Supported commands: selfcheck, ui, api', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
