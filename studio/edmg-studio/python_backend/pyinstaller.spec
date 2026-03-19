# PyInstaller spec for the EDMG Studio backend.
# Build from: studio/edmg-studio/python_backend

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

here = Path(os.getcwd()).resolve()

hidden = []
hidden += collect_submodules("uvicorn")
hidden += collect_submodules("fastapi")
hidden += collect_submodules("pydantic")
hidden += collect_submodules("keyring")
hidden += collect_submodules("yaml")
hidden += collect_submodules("nltk")
hidden += collect_submodules("textblob")
hidden += collect_submodules("spacy")

# Explicit first-party packages used by the backend.
hidden += collect_submodules("edmg_studio_backend")
hidden += collect_submodules("edmg_ai_service")
hidden += collect_submodules("enhanced_deforum_music_generator")
hidden += collect_submodules("deforum_music")
hidden += collect_submodules("edmg")
hidden += collect_submodules("core")
hidden += collect_submodules("config")
hidden += collect_submodules("integrations")

a = Analysis(
    [str(here / "backend_entry.py")],
    pathex=[str(here)],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="edmg-studio-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
