# file: src/enhanced_deforum_music_generator/config/config_system.py
import yaml, os
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class AudioConfig:
    chunk_size: int = 30
    chunk_threshold: int = 60
    max_duration: int = 1800  # 30 minutes default as requested
    cache_dir: Optional[str] = None
    sample_rate: int = 22050

@dataclass
class LyricsConfig:
    provider: str = "whisper"
    model: str = "tiny"
    language: Optional[str] = None

@dataclass
class AnimationConfig:
    fps: int = 30
    duration: int = 60
    resolution: str = "512x512"

@dataclass
class A1111Config:
    host: str = "127.0.0.1"
    port: int = 7860
    timeout: int = 120

@dataclass
class InterfaceConfig:
    server_port: int = 7862
    enable_sharing: bool = False
    auto_open_browser: bool = True
    show_error_details: bool = True

@dataclass
class AdvancedConfig:
    enable_multitrack: bool = False
    enable_style_transfer: bool = False
    enable_preview: bool = True
    enable_cloud_storage: bool = False
    cloud_provider: str = "aws"

@dataclass
class Config:
    audio: AudioConfig = field(default_factory=AudioConfig)
    lyrics: LyricsConfig = field(default_factory=LyricsConfig)
    animation: AnimationConfig = field(default_factory=AnimationConfig)
    a1111: A1111Config = field(default_factory=A1111Config)
    interface: InterfaceConfig = field(default_factory=InterfaceConfig)
    advanced: AdvancedConfig = field(default_factory=AdvancedConfig)

    @classmethod
    def from_yaml(cls, path: str) -> "Config":
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(
            audio=AudioConfig(**data.get("audio", {})),
            lyrics=LyricsConfig(**data.get("lyrics", {})),
            animation=AnimationConfig(**data.get("animation", {})),
            a1111=A1111Config(**data.get("a1111", {})),
            interface=InterfaceConfig(**data.get("interface", {})),
            advanced=AdvancedConfig(**data.get("advanced", {})),
        )

def load_config(path: Optional[str] = None):
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "..", "..", "custom.yaml")
        path = os.path.abspath(path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        print(f"[WARN] Could not load config from {path}: {e}")
        return {}
