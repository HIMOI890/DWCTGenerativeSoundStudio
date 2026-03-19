from __future__ import annotations

import argparse
import uvicorn


def main() -> None:
    p = argparse.ArgumentParser(prog="edmg-ai", description="Run EDMG AI Service (local-first).")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="Run the HTTP server.")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=7862)
    s.add_argument("--reload", action="store_true")

    args = p.parse_args()
    if args.cmd == "serve":
        uvicorn.run("edmg_ai_service.app:app", host=args.host, port=args.port, reload=args.reload)
