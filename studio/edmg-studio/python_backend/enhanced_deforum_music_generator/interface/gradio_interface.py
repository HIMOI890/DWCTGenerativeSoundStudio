"""Gradio UI bridge for the vendored EDMG engine.

The standalone Gradio experience is intentionally lightweight in the Studio
repo. This module exposes the historical ``create_interface()`` symbol used
by deployment scripts and delegates to ``deforum_music.core``.
"""

from __future__ import annotations

from typing import Any


def create_interface() -> Any:
    from deforum_music.core import create_gradio_interface
    return create_gradio_interface()


def launch_interface(**launch_kwargs: Any) -> Any:
    app = create_interface()
    app.launch(**launch_kwargs)
    return app


__all__ = ['create_interface', 'launch_interface']
