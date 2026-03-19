
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import UserFacingError

try:
    import numpy as np  # type: ignore
    from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont
except Exception:  # pragma: no cover
    np = None  # type: ignore
    Image = None  # type: ignore
    ImageColor = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFilter = None  # type: ignore
    ImageFont = None  # type: ignore


_BLEND_MODES = {"normal", "multiply", "screen", "overlay"}


@dataclass(frozen=True)
class Layer:
    """Timeline compositing layer.

    Base fields:
      - type: image|text
      - start_s/end_s: seconds
      - z: draw order

    Common data fields (can be keyframed):
      - x, y
      - opacity (0..1)
      - rotation_deg
      - blend_mode: normal|multiply|screen|overlay
      - mask_asset: filename in assets/masks (optional)
      - mask_invert: bool
      - mask_feather_px: int
      - mask_x, mask_y: offset (px) from layer center
      - mask_scale: uniform scale
      - mask_rotation_deg: rotation (deg)
      - keyframes: [{t, ...props...}, ...]  # per-layer animation

    Image-only:
      - asset: filename in assets/overlays
      - w, h (optional)

    Text-only:
      - text, size, color, stroke_color, stroke_width
    """
    type: str
    start_s: float
    end_s: float
    z: int = 0
    data: dict[str, Any] | None = None


class _AssetCache:
    _imgs: dict[str, Any] = {}

    @classmethod
    def get(cls, key: str) -> Any | None:
        return cls._imgs.get(key)

    @classmethod
    def set(cls, key: str, img: Any) -> None:
        cls._imgs[key] = img


def _require_pillow() -> None:
    if Image is None or np is None:
        raise UserFacingError(
            "Internal compositing deps missing",
            hint="Install backend deps including Pillow + numpy, then retry.",
            code="INTERNAL_DEPS",
            status_code=500,
        )


def _to_rgba(im: "Image.Image") -> "Image.Image":
    return im.convert("RGBA") if im.mode != "RGBA" else im


def _safe_filename(name: str) -> str:
    return name.replace("\\", "/").lstrip("/")


def _load_rgba(project_dir: Path, *, kind: str, name: str) -> "Image.Image":
    _require_pillow()
    name = _safe_filename(name)
    p = project_dir / "assets" / kind / name
    if not p.exists():
        raise UserFacingError(
            f"Asset not found: {kind}/{name}",
            hint="Upload it again in Render → Overlays/Masks.",
            code="ASSET_MISSING",
            status_code=400,
        )
    key = str(p.resolve())
    cached = _AssetCache.get(key)
    if cached is not None:
        return cached.copy()
    img = Image.open(p).convert("RGBA")
    _AssetCache.set(key, img)
    return img.copy()


def _parse_layers(raw: Any) -> list[Layer]:
    layers: list[Layer] = []
    if not raw:
        return layers
    if isinstance(raw, dict):
        raw = raw.get("layers") or []
    if not isinstance(raw, list):
        return layers
    for it in raw:
        if not isinstance(it, dict):
            continue
        layers.append(
            Layer(
                type=str(it.get("type") or "image"),
                start_s=float(it.get("start_s", 0.0)),
                end_s=float(it.get("end_s", 1e12)),
                z=int(it.get("z", 0)),
                data=dict(it),
            )
        )
    layers.sort(key=lambda x: x.z)
    return layers


def _smoothstep(u: float) -> float:
    u = max(0.0, min(1.0, float(u)))
    return u * u * (3.0 - 2.0 * u)


def _lerp(a: float, b: float, w: float) -> float:
    return a * (1.0 - w) + b * w


def _keyframes(data: dict[str, Any]) -> list[dict[str, Any]]:
    kf = data.get("keyframes")
    if isinstance(kf, list):
        out = [x for x in kf if isinstance(x, dict) and "t" in x]
        out.sort(key=lambda d: float(d.get("t", 0.0)))
        return out
    return []


def _eval_props_at(data: dict[str, Any], t: float) -> dict[str, Any]:
    """Evaluate possibly-keyframed properties for a layer at time t.

    Numeric properties are interpolated; strings are held from nearest prior keyframe.
    """
    kfs = _keyframes(data)
    if not kfs:
        return dict(data)

    # Base props as defaults
    base = dict(data)

    if t <= float(kfs[0]["t"]):
        merged = dict(base)
        merged.update(kfs[0])
        return merged

    if t >= float(kfs[-1]["t"]):
        merged = dict(base)
        merged.update(kfs[-1])
        return merged

    a, b = kfs[0], kfs[-1]
    for i in range(len(kfs) - 1):
        if float(kfs[i]["t"]) <= t <= float(kfs[i + 1]["t"]):
            a, b = kfs[i], kfs[i + 1]
            break

    ta, tb = float(a["t"]), float(b["t"])
    u = (t - ta) / max(1e-9, (tb - ta))
    w = _smoothstep(u)

    merged = dict(base)

    # interpolate common numeric fields if present
    for key in ["x", "y", "w", "h", "opacity", "rotation_deg", "scale", "mask_x", "mask_y", "mask_scale", "mask_rotation_deg"]:
        va = a.get(key, merged.get(key))
        vb = b.get(key, va)
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            merged[key] = _lerp(float(va), float(vb), w)

    # carry-forward string-ish fields from nearest prior keyframe
    for key in ["text", "color", "stroke_color", "asset", "blend_mode", "mask_asset"]:
        if key in b and w > 0.5:
            merged[key] = b[key]
        elif key in a:
            merged[key] = a[key]

    # booleans / ints
    for key in ["mask_invert", "stroke_width", "size", "mask_feather_px", "camera_follow"]:
        if key in b and w > 0.5:
            merged[key] = b[key]
        elif key in a:
            merged[key] = a[key]

    return merged


def _render_text_rgba(size: tuple[int, int], data: dict[str, Any]) -> "Image.Image":
    _require_pillow()
    W, H = size
    text = str(data.get("text") or "")
    if not text:
        return Image.new("RGBA", (W, H), (0, 0, 0, 0))

    x = int(data.get("x", 0))
    y = int(data.get("y", 0))
    font_size = int(data.get("size", 28))
    color = str(data.get("color", "#ffffff"))
    stroke_color = data.get("stroke_color")
    stroke_width = int(data.get("stroke_width", 0))
    rotation = float(data.get("rotation_deg", 0.0))

    fill = ImageColor.getrgb(color) if ImageColor else (255, 255, 255)
    sc = ImageColor.getrgb(str(stroke_color)) if (stroke_color and ImageColor) else None

    # render on a tight canvas, then rotate and paste
    tmp = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tmp)

    try:
        font = ImageFont.truetype("arial.ttf", size=font_size)
    except Exception:
        font = ImageFont.load_default()

    draw.text(
        (x, y),
        text,
        fill=fill + (255,),
        font=font,
        stroke_width=stroke_width if stroke_width > 0 else 0,
        stroke_fill=(sc + (255,)) if sc else None,
    )

    if abs(rotation) > 0.01:
        tmp = tmp.rotate(rotation, resample=Image.BICUBIC, expand=False)
    return tmp


def _render_image_rgba(project_dir: Path, canvas_size: tuple[int, int], data: dict[str, Any]) -> "Image.Image":
    _require_pillow()
    W, H = canvas_size
    asset = str(data.get("asset") or data.get("path") or "").strip()
    if not asset:
        return Image.new("RGBA", (W, H), (0, 0, 0, 0))

    x = int(data.get("x", 0))
    y = int(data.get("y", 0))
    w = data.get("w")
    h = data.get("h")
    scale = float(data.get("scale", 1.0))
    opacity = float(data.get("opacity", 1.0))
    rotation = float(data.get("rotation_deg", 0.0))

    img = _load_rgba(project_dir, kind="overlays", name=asset)

    if w and h:
        img = img.resize((int(w), int(h)), resample=Image.LANCZOS)
    if abs(scale - 1.0) > 1e-3:
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), resample=Image.LANCZOS)
    if abs(rotation) > 0.01:
        img = img.rotate(rotation, resample=Image.BICUBIC, expand=True)

    # apply opacity to alpha
    opacity = max(0.0, min(1.0, opacity))
    if opacity < 0.999:
        r, g, b, a = img.split()
        a = a.point(lambda v: int(round(v * opacity)))
        img = Image.merge("RGBA", (r, g, b, a))

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    overlay.paste(img, (x, y), img)
    return overlay


def _apply_mask_alpha(project_dir: Path, overlay_rgba: "Image.Image", data: dict[str, Any]) -> "Image.Image":
    """Apply a grayscale mask to the overlay alpha.

    Mask transform fields (optional; editable via Studio gizmos):
      - mask_asset: filename in assets/masks
      - mask_invert: bool
      - mask_feather_px: int
      - mask_x, mask_y: offset (px) from layer center
      - mask_scale: uniform scale
      - mask_rotation_deg: rotation (deg)
      - mask_x / mask_y: offset (px) from the layer center
      - mask_scale: uniform scale (default 1.0)
      - mask_rotation_deg: rotation (deg) around mask center

    By default the mask is fit to the *layer bounds* (w/h) and centered on the layer.
    """
    _require_pillow()
    mask_name = str(data.get("mask_asset") or "").strip()
    if not mask_name:
        return overlay_rgba

    # Layer geometry (overlay was pasted at x/y with size w/h onto a full-canvas RGBA)
    W, H = overlay_rgba.size
    x = int(float(data.get("x", 0) or 0))
    y = int(float(data.get("y", 0) or 0))
    lw = int(float(data.get("w", 0) or 0))
    lh = int(float(data.get("h", 0) or 0))
    if lw <= 0 or lh <= 0:
        # Fallback: treat mask as full-canvas
        lw, lh = W, H
        x, y = 0, 0

    # Transform params
    mx = float(data.get("mask_x", 0.0) or 0.0)
    my = float(data.get("mask_y", 0.0) or 0.0)
    mscale = float(data.get("mask_scale", 1.0) or 1.0)
    mrot = float(data.get("mask_rotation_deg", 0.0) or 0.0)

    # Build a full-canvas mask by placing a transformed mask image onto it
    src = _load_rgba(project_dir, kind="masks", name=mask_name).convert("L")
    tw = max(1, int(round(lw * max(0.05, mscale))))
    th = max(1, int(round(lh * max(0.05, mscale))))
    src = src.resize((tw, th), resample=Image.BILINEAR)

    if bool(data.get("mask_invert", False)):
        src = Image.eval(src, lambda v: 255 - v)

    if abs(mrot) > 1e-6:
        src = src.rotate(mrot, resample=Image.BICUBIC, expand=True)

    # Position: mask center tracks layer center + (mask_x, mask_y)
    layer_cx = x + (lw / 2.0)
    layer_cy = y + (lh / 2.0)
    mcx = layer_cx + mx
    mcy = layer_cy + my
    px = int(round(mcx - (src.size[0] / 2.0)))
    py = int(round(mcy - (src.size[1] / 2.0)))

    mask_canvas = Image.new("L", (W, H), 0)
    mask_canvas.paste(src, (px, py))

    feather = int(data.get("mask_feather_px", 0) or 0)
    if feather > 0 and ImageFilter:
        mask_canvas = mask_canvas.filter(ImageFilter.GaussianBlur(radius=feather))

    # multiply overlay alpha by mask
    r, g, b, a = overlay_rgba.split()
    a_np = np.asarray(a, dtype=np.float32) / 255.0
    m_np = np.asarray(mask_canvas, dtype=np.float32) / 255.0
    out_a = np.clip(a_np * m_np, 0.0, 1.0)
    a2 = Image.fromarray((out_a * 255.0).astype(np.uint8), mode="L")
    return Image.merge("RGBA", (r, g, b, a2))


def _blend_rgb(base_rgb: "Image.Image", overlay_rgba: "Image.Image", blend_mode: str) -> "Image.Image":
    _require_pillow()
    blend_mode = (blend_mode or "normal").lower().strip()
    if blend_mode not in _BLEND_MODES:
        blend_mode = "normal"

    B = np.asarray(base_rgb.convert("RGB"), dtype=np.float32) / 255.0
    O = np.asarray(overlay_rgba.convert("RGBA"), dtype=np.float32) / 255.0
    Orgb = O[..., :3]
    A = O[..., 3:4]  # 0..1

    if blend_mode == "normal":
        C = Orgb
    elif blend_mode == "multiply":
        C = B * Orgb
    elif blend_mode == "screen":
        C = 1.0 - (1.0 - B) * (1.0 - Orgb)
    elif blend_mode == "overlay":
        C = np.where(B <= 0.5, 2.0 * B * Orgb, 1.0 - 2.0 * (1.0 - B) * (1.0 - Orgb))
    else:
        C = Orgb

    out = B * (1.0 - A) + C * A
    out = np.clip(out, 0.0, 1.0)
    return Image.fromarray((out * 255.0).astype(np.uint8), mode="RGB")


def apply_timeline_layers(frame: "Image.Image", *, project_dir: Path, timeline: Any, t: float) -> "Image.Image":
    """Apply compositing layers for time t.

    Backward compatible timeline:
      timeline = {"layers":[...]}
    Extended timeline:
      timeline = {"layers":[...], "camera":{...}}
    """
    _require_pillow()
    layers = _parse_layers(timeline)
    if not layers:
        return frame.convert("RGB")

    base = frame.convert("RGB")
    W, H = base.size

    for layer in layers:
        if t < layer.start_s or t >= layer.end_s:
            continue
        data0 = layer.data or {}
        data = _eval_props_at(data0, t)

        opacity = float(data.get("opacity", 1.0))
        if opacity <= 0.001:
            continue

        blend_mode = str(data.get("blend_mode") or "normal").lower()
        if blend_mode not in _BLEND_MODES:
            blend_mode = "normal"

        if layer.type == "text":
            overlay = _render_text_rgba((W, H), data)
        elif layer.type == "image":
            overlay = _render_image_rgba(project_dir, (W, H), data)
        else:
            continue

        # apply mask if present
        overlay = _apply_mask_alpha(project_dir, overlay, data)
        # blend
        base = _blend_rgb(base, overlay, blend_mode)

    return base
