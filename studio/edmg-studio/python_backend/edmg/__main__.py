from __future__ import annotations
import sys

def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "selfcheck"
    if cmd in {"selfcheck", "check"}:
        from enhanced_deforum_music_generator.cli.selfcheck import run
        return run()
    print(f"Unknown command: {cmd}", file=sys.stderr)
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
