"""Run the EDMG FastAPI server.

This is the backend that the Electron GUI talks to.

Usage:
  python -m scripts.run_api

Env:
  EDMG_API_HOST (default: 127.0.0.1)
  EDMG_API_PORT (default: 7862)
"""

from __future__ import annotations

import os


def main() -> None:
    host = os.environ.get("EDMG_API_HOST", "127.0.0.1")
    port = int(os.environ.get("EDMG_API_PORT", "7862"))

    import uvicorn

    uvicorn.run(
        "enhanced_deforum_music_generator.api.main:app",
        host=host,
        port=port,
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    main()
