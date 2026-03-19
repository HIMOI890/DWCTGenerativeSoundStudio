export type StoredRenderProfileId = "laptop_safe" | "balanced_auto" | "high_quality";

export type StudioRenderDefaults = {
  profileId?: StoredRenderProfileId;
  renderPreset?: "fast" | "balanced" | "quality" | "ultra";
  internalRenderTier?: "auto" | "draft" | "balanced" | "quality";
  internalResumeExisting?: boolean;
};

const KEY = "edmg_render_defaults";

export function readRenderDefaults(): StudioRenderDefaults {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return {};
    return parsed as StudioRenderDefaults;
  } catch {
    return {};
  }
}

export function writeRenderDefaults(value: StudioRenderDefaults) {
  localStorage.setItem(KEY, JSON.stringify(value || {}));
}

export function clearRenderDefaults() {
  localStorage.removeItem(KEY);
}
