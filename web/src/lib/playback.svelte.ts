// Shared playback controller for one Transcript view. Player.svelte owns the
// actual <audio>/<video> element and assigns it here; everything else (the
// transcript body's highlight loop, keyboard shortcuts, the scrubber) drives
// playback through this object instead of poking the DOM directly.
//
// `currentTime` / `isPlaying` are reactive and cheap to read in templates
// (updated at the media element's native `timeupdate` rate, a few times a
// second) — they are NOT used by the word-highlight loop, which reads
// `mediaEl.currentTime` directly every animation frame to avoid any
// per-word reactivity or re-render.

export const RATES = [1, 1.25, 1.5, 1.75, 2] as const;

export class Playback {
  mediaEl = $state<HTMLMediaElement | null>(null);
  isPlaying = $state(false);
  currentTime = $state(0);
  duration = $state(0);
  rate = $state<(typeof RATES)[number]>(1);

  private listenersAttached: HTMLMediaElement | null = null;

  attach(el: HTMLMediaElement | null) {
    if (this.listenersAttached === el) return;
    if (this.listenersAttached) this.detachListeners(this.listenersAttached);
    this.mediaEl = el;
    if (el) {
      el.playbackRate = this.rate;
      el.addEventListener('play', this.onPlay);
      el.addEventListener('pause', this.onPause);
      el.addEventListener('timeupdate', this.onTimeUpdate);
      el.addEventListener('durationchange', this.onDurationChange);
      this.listenersAttached = el;
    }
  }

  private detachListeners(el: HTMLMediaElement) {
    el.removeEventListener('play', this.onPlay);
    el.removeEventListener('pause', this.onPause);
    el.removeEventListener('timeupdate', this.onTimeUpdate);
    el.removeEventListener('durationchange', this.onDurationChange);
  }

  private onPlay = () => {
    this.isPlaying = true;
  };
  private onPause = () => {
    this.isPlaying = false;
  };
  private onTimeUpdate = () => {
    if (this.mediaEl) this.currentTime = this.mediaEl.currentTime;
  };
  private onDurationChange = () => {
    if (this.mediaEl && Number.isFinite(this.mediaEl.duration)) this.duration = this.mediaEl.duration;
  };

  seek(time: number, opts: { play?: boolean } = {}) {
    const el = this.mediaEl;
    if (!el) return;
    const clamped = Math.max(0, Math.min(time, this.duration || time));
    el.currentTime = clamped;
    this.currentTime = clamped;
    if (opts.play) void el.play();
  }

  playPause() {
    const el = this.mediaEl;
    if (!el) return;
    if (el.paused) void el.play();
    else el.pause();
  }

  skip(deltaSec: number) {
    const el = this.mediaEl;
    if (!el) return;
    this.seek(el.currentTime + deltaSec);
  }

  cycleRate() {
    const idx = RATES.indexOf(this.rate);
    const next = RATES[(idx + 1) % RATES.length];
    this.rate = next;
    if (this.mediaEl) this.mediaEl.playbackRate = next;
  }
}
