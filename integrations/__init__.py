"""Compatibility shim.

Tests and older code import `integrations.*`. This package re-exports from
`enhanced_deforum_music_generator.integrations`.
"""

from importlib import import_module as _import_module

_target = "enhanced_deforum_music_generator.integrations"
_mod = _import_module(_target)

def __getattr__(name):
    return getattr(_mod, name)

def __dir__():
    return sorted(set(globals().keys()) | set(dir(_mod)))
