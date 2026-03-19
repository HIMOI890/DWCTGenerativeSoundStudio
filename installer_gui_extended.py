#!/usr/bin/env python3
"""Extended installer entrypoint.

This file exists so users can run:
  python installer_gui_extended.py

It forwards to the main GUI (installer_gui.py), which now includes extra tabs.
"""

from __future__ import annotations

import installer_gui


if __name__ == "__main__":
    installer_gui.main()
