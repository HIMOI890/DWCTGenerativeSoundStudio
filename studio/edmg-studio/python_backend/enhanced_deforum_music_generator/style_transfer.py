from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional


@dataclass(frozen=True)
class StylePreset:
    name: str
    modifiers: List[str]
    image_profile: Dict[str, float]


class StyleTransferEngine:
    def __init__(self):
        self._styles = {
            'cinematic': StylePreset(
                name='cinematic',
                modifiers=['cinematic lighting', 'dramatic composition', 'film grain', 'depth of field'],
                image_profile={'contrast': 1.18, 'color': 0.92, 'sharpness': 1.08, 'brightness': 0.98},
            ),
            'anime': StylePreset(
                name='anime',
                modifiers=['anime style', 'cel shading', 'clean line art', 'vibrant colors'],
                image_profile={'contrast': 1.08, 'color': 1.35, 'sharpness': 1.15, 'posterize_bits': 6},
            ),
            'photorealistic': StylePreset(
                name='photorealistic',
                modifiers=['photorealistic', 'natural lighting', 'high detail', 'sharp focus'],
                image_profile={'contrast': 1.06, 'color': 1.02, 'sharpness': 1.22, 'brightness': 1.02},
            ),
            'abstract': StylePreset(
                name='abstract',
                modifiers=['abstract art', 'surreal composition', 'fluid shapes', 'expressive motion'],
                image_profile={'contrast': 1.24, 'color': 1.28, 'sharpness': 0.92, 'solarize_threshold': 190},
            ),
            'vintage': StylePreset(
                name='vintage',
                modifiers=['vintage film', 'retro aesthetic', 'aged tones', 'analog texture'],
                image_profile={'contrast': 0.96, 'color': 0.78, 'sharpness': 0.9, 'sepia': 0.75},
            ),
        }

    def available_styles(self) -> List[str]:
        return sorted(self._styles.keys())

    def get_style(self, style_name: str) -> StylePreset:
        key = str(style_name or '').strip().lower()
        if key not in self._styles:
            raise ValueError(f'Unknown style: {style_name}')
        return self._styles[key]

    def apply_style_to_prompts(
        self,
        prompts: Mapping[Any, str],
        style_name: str,
        strength: float = 0.7,
        extra_modifiers: Optional[List[str]] = None,
    ) -> Dict[Any, str]:
        preset = self.get_style(style_name)
        strength = max(0.0, min(1.0, float(strength)))
        count = max(1, round(len(preset.modifiers) * strength)) if preset.modifiers else 0
        modifiers = list(preset.modifiers[:count])
        if extra_modifiers:
            modifiers.extend(str(x).strip() for x in extra_modifiers if str(x).strip())
        if not modifiers:
            return {k: str(v) for k, v in prompts.items()}
        suffix = ', ' + ', '.join(modifiers)
        return {key: (str(value).strip() + suffix).strip(', ') for key, value in prompts.items()}

    def apply_style_to_image(self, image: Any, style_name: str, strength: float = 0.7):
        from PIL import Image, ImageEnhance, ImageOps, ImageFilter

        preset = self.get_style(style_name)
        strength = max(0.0, min(1.0, float(strength)))
        img = Image.open(image).convert('RGB') if isinstance(image, (str, Path)) else image.convert('RGB')
        profile = dict(preset.image_profile)

        if 'contrast' in profile:
            factor = 1.0 + ((profile['contrast'] - 1.0) * strength)
            img = ImageEnhance.Contrast(img).enhance(factor)
        if 'color' in profile:
            factor = 1.0 + ((profile['color'] - 1.0) * strength)
            img = ImageEnhance.Color(img).enhance(factor)
        if 'brightness' in profile:
            factor = 1.0 + ((profile['brightness'] - 1.0) * strength)
            img = ImageEnhance.Brightness(img).enhance(factor)
        if 'sharpness' in profile:
            factor = 1.0 + ((profile['sharpness'] - 1.0) * strength)
            img = ImageEnhance.Sharpness(img).enhance(factor)
        if 'posterize_bits' in profile:
            base_bits = int(round(8 - ((8 - int(profile['posterize_bits'])) * strength)))
            img = ImageOps.posterize(img, max(2, min(8, base_bits)))
        if 'solarize_threshold' in profile:
            threshold = int(round(255 - ((255 - int(profile['solarize_threshold'])) * strength)))
            img = ImageOps.solarize(img, threshold=max(0, min(255, threshold)))
        if 'sepia' in profile:
            sepia_strength = max(0.0, min(1.0, float(profile['sepia']) * strength))
            sepia = ImageOps.colorize(ImageOps.grayscale(img), '#2e1a0f', '#f1d3a2')
            img = Image.blend(img, sepia, sepia_strength)
        if preset.name == 'cinematic':
            img = img.filter(ImageFilter.UnsharpMask(radius=1.0, percent=int(80 * max(strength, 0.25)), threshold=2))
        elif preset.name == 'abstract':
            img = img.filter(ImageFilter.EDGE_ENHANCE_MORE)
        return img

    def transfer(self, image_path: str | Path, output_path: str | Path, style_name: str, strength: float = 0.7) -> str:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        img = self.apply_style_to_image(image_path, style_name=style_name, strength=strength)
        img.save(out)
        return str(out)


class StyleTransfer:
    def __init__(self, engine: Optional[StyleTransferEngine] = None):
        self.engine = engine or StyleTransferEngine()

    def apply_to_prompts(self, prompts: Mapping[Any, str], style_name: str, strength: float = 0.7, extra_modifiers: Optional[List[str]] = None) -> Dict[Any, str]:
        return self.engine.apply_style_to_prompts(prompts, style_name=style_name, strength=strength, extra_modifiers=extra_modifiers)

    def apply_to_image(self, image: Any, style_name: str, strength: float = 0.7):
        return self.engine.apply_style_to_image(image, style_name=style_name, strength=strength)

    def transfer(self, image_path: str | Path, output_path: str | Path, style_name: str, strength: float = 0.7) -> str:
        return self.engine.transfer(image_path=image_path, output_path=output_path, style_name=style_name, strength=strength)


__all__ = ['StyleTransfer', 'StyleTransferEngine', 'StylePreset']
