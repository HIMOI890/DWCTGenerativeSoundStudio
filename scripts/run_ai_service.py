"""Run the embedded EDMG AI service (FastAPI) for local orchestration.

Usage:
  python -m scripts.run_ai_service --host 127.0.0.1 --port 7862
"""

from __future__ import annotations

import argparse
import os

import uvicorn

from services.ai.edmg_ai_service.app import app  # type: ignore


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default=os.getenv("EDMG_AI_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.getenv("EDMG_AI_PORT", "7862")))
    args = p.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
