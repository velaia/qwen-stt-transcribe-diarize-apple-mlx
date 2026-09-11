// Stable, muted, distinguishable hues per speaker. CSS supplies saturation
// and lightness per theme (see --speaker-sat / --speaker-light in app.css);
// this module only ever hands out a hue so light/dark stay in sync.

const HUES = [212, 18, 158, 275, 42, 190, 340, 96, 258, 12, 172, 320];

const cache = new Map<string, number>();

/** `speakers` should be the canonical, stable order (Result.speakers / Summary.speakers). */
export function speakerHue(label: string | null, speakers: string[]): number {
  if (label === null) return 220; // muted neutral for non-diarized text
  const key = label;
  const hit = cache.get(key);
  if (hit !== undefined) return hit;
  const idx = speakers.indexOf(label);
  const hue = HUES[(idx >= 0 ? idx : hashCode(label)) % HUES.length];
  cache.set(key, hue);
  return hue;
}

function hashCode(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

const DEFAULT_NAME_RE = /^SPEAKER_0*(\d+)$/i;

/** Default display name for a raw label with no override: SPEAKER_00 -> "Speaker 1". */
export function defaultSpeakerName(label: string): string {
  const m = DEFAULT_NAME_RE.exec(label);
  if (m) return `Speaker ${Number(m[1]) + 1}`;
  return label;
}

export function speakerDisplayName(label: string, names: Record<string, string>): string {
  const override = names[label];
  return override && override.trim() ? override : defaultSpeakerName(label);
}
