export type DesktopArtifactActionMode = 'reveal' | 'open';
export type DesktopPlatformKind = 'windows' | 'mac' | 'linux' | 'other';

export type DesktopArtifactActionResult = {
  ok: boolean;
  action?: string;
  path?: string;
  error?: string;
  fallbackCopied?: boolean;
  platform?: DesktopPlatformKind;
  bridgeAvailable?: boolean;
};

function detectPlatformString(): string {
  const nav: any = typeof navigator !== 'undefined' ? navigator : undefined;
  const uaPlatform = nav?.userAgentData?.platform;
  return String(uaPlatform || nav?.platform || nav?.userAgent || '').toLowerCase();
}

export function getDesktopPlatformKind(): DesktopPlatformKind {
  const raw = detectPlatformString();
  if (raw.includes('win')) return 'windows';
  if (raw.includes('mac') || raw.includes('darwin')) return 'mac';
  if (raw.includes('linux') || raw.includes('x11')) return 'linux';
  return 'other';
}

export function hasDesktopPathBridge(): boolean {
  if (typeof window === 'undefined') return false;
  return Boolean(window.edmg && (window.edmg.revealPath || window.edmg.showItemInFolder || window.edmg.openPath));
}

function copyLabel(noun: string): string {
  const trimmed = noun.trim();
  const lower = trimmed.toLowerCase();
  if (lower.includes('path') || lower.includes('dir') || lower.includes('folder')) return `Copy ${trimmed}`;
  return `Copy ${trimmed} path`;
}

export function desktopActionLabel(mode: DesktopArtifactActionMode, noun: string): string {
  if (!hasDesktopPathBridge()) return copyLabel(noun);
  const platform = getDesktopPlatformKind();
  const suffix = platform === 'windows'
    ? 'Explorer'
    : platform === 'mac'
      ? 'Finder'
      : platform === 'linux'
        ? 'File Manager'
        : 'location';
  if (mode === 'open') return suffix === 'location' ? `Open ${noun}` : `Open ${noun} in ${suffix}`;
  return suffix === 'location' ? `Reveal ${noun}` : `Reveal ${noun} in ${suffix}`;
}

export async function copyPathValue(label: string, value?: string | null): Promise<DesktopArtifactActionResult> {
  if (!value) return { ok: false, error: `Missing ${label}` };
  if (!navigator?.clipboard?.writeText) {
    return { ok: false, error: 'Clipboard API unavailable' };
  }
  await navigator.clipboard.writeText(value);
  return {
    ok: true,
    action: 'copied_path',
    path: value,
    fallbackCopied: true,
    platform: getDesktopPlatformKind(),
    bridgeAvailable: hasDesktopPathBridge(),
  };
}

export async function runDesktopArtifactAction(
  label: string,
  value: string | null | undefined,
  mode: DesktopArtifactActionMode,
): Promise<DesktopArtifactActionResult> {
  if (!value) return { ok: false, error: `Missing ${label}` };
  const bridgeAvailable = hasDesktopPathBridge();
  const platform = getDesktopPlatformKind();
  const fn = mode === 'open'
    ? window.edmg?.openPath
    : (window.edmg?.revealPath || window.edmg?.showItemInFolder);
  if (!bridgeAvailable || !fn) {
    const copied = await copyPathValue(label, value);
    if (!copied.ok) return copied;
    return { ...copied, action: 'copied_path_fallback', bridgeAvailable, platform };
  }
  const result = await fn(value);
  if (result && typeof result === 'object' && 'ok' in result && !result.ok) {
    return {
      ok: false,
      error: result.error || `Unable to ${mode} ${label}`,
      path: value,
      bridgeAvailable,
      platform,
    };
  }
  return {
    ok: true,
    action: result?.action || (mode === 'open' ? 'open_path' : 'reveal_path'),
    path: result?.path || value,
    bridgeAvailable,
    platform,
  };
}
