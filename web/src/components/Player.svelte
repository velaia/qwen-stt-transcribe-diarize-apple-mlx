<script lang="ts">
  import { mediaAudioUrl, mediaOriginalUrl } from '../lib/api';
  import { cacheKey, clearAll, fetchAndCache, getCachedBlob } from '../lib/mediaCache';
  import { formatClock } from '../lib/format';
  import { speakerHue } from '../lib/speakerColor';
  import { Playback, RATES } from '../lib/playback.svelte';

  interface TimelineTurn {
    start: number;
    end: number;
    speaker: string | null;
  }

  interface Props {
    id: string;
    audioVersion: string | null;
    audioReady: boolean;
    playableOriginal: boolean;
    duration: number;
    speakers: string[];
    turns: TimelineTurn[];
    playback: Playback;
  }

  let { id, audioVersion, audioReady, playableOriginal, duration, speakers, turns, playback }: Props = $props();

  let mediaEl: HTMLMediaElement | undefined = $state();
  let showVideo = $state(false);
  let objectUrl: string | null = null;
  let cacheStatus = $state<'streaming' | 'caching' | 'cached' | 'unavailable'>(
    audioReady ? 'streaming' : 'unavailable'
  );
  let cacheProgress = $state(0);
  let cacheMenuOpen = $state(false);

  // Narrow screens get fewer skip buttons.
  let windowWidth = $state(typeof window !== 'undefined' ? window.innerWidth : 1024);
  let narrow = $derived(windowWidth < 480);

  $effect(() => {
    playback.attach(mediaEl ?? null);
  });

  function swapToBlob(blob: Blob) {
    const url = URL.createObjectURL(blob);
    const doSwap = () => {
      if (!mediaEl) return;
      const t = mediaEl.currentTime;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = url;
      mediaEl.src = url;
      mediaEl.currentTime = t;
      cacheStatus = 'cached';
    };
    if (!mediaEl || mediaEl.paused) {
      doSwap();
    } else {
      mediaEl.addEventListener('pause', doSwap, { once: true });
    }
  }

  $effect(() => {
    if (showVideo || !audioReady || !mediaEl) return;
    let cancelled = false;
    const key = cacheKey(id, audioVersion);

    (async () => {
      const cached = await getCachedBlob(key);
      if (cancelled) return;
      if (cached) {
        const url = URL.createObjectURL(cached);
        objectUrl = url;
        if (mediaEl) mediaEl.src = url;
        cacheStatus = 'cached';
        return;
      }
      if (mediaEl) mediaEl.src = mediaAudioUrl(id);
      cacheStatus = 'caching';
      const blob = await fetchAndCache(mediaAudioUrl(id), key, (f) => {
        cacheProgress = f;
      });
      if (cancelled) return;
      if (blob) swapToBlob(blob);
      else cacheStatus = 'streaming';
    })();

    return () => {
      cancelled = true;
    };
  });

  $effect(() => {
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  });

  async function onClearCache() {
    await clearAll();
    cacheStatus = 'streaming';
    cacheMenuOpen = false;
  }

  function toggleVideo() {
    const t = playback.currentTime;
    const wasPlaying = playback.isPlaying;
    showVideo = !showVideo;
    queueMicrotask(() => {
      if (!mediaEl) return;
      mediaEl.currentTime = t;
      if (wasPlaying) void mediaEl.play();
    });
  }

  // --- Timeline (scrubber + speaker bands) -------------------------------
  let timelineWrap: HTMLDivElement | undefined = $state();
  let bandsCanvas: HTMLCanvasElement | undefined = $state();
  let markerCanvas: HTMLCanvasElement | undefined = $state();
  let timelineWidth = $state(0);
  const timelineHeight = 36;
  let dragging = $state(false);
  let hoverFraction = $state<number | null>(null);

  function drawBands() {
    if (!bandsCanvas || !timelineWidth || !duration) return;
    const dpr = window.devicePixelRatio || 1;
    bandsCanvas.width = timelineWidth * dpr;
    bandsCanvas.height = timelineHeight * dpr;
    const ctx = bandsCanvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, timelineWidth, timelineHeight);
    const isDark = window.matchMedia?.('(prefers-color-scheme: dark)').matches;
    const sat = isDark ? 55 : 42;
    const light = isDark ? 60 : 42;
    for (const turn of turns) {
      const x = (turn.start / duration) * timelineWidth;
      const w = Math.max(1, ((turn.end - turn.start) / duration) * timelineWidth);
      const hue = speakerHue(turn.speaker, speakers);
      ctx.fillStyle = `hsl(${hue} ${sat}% ${light}% / 0.7)`;
      ctx.fillRect(x, 6, w, timelineHeight - 12);
    }
  }

  function drawMarker() {
    if (!markerCanvas || !timelineWidth) return;
    const dpr = window.devicePixelRatio || 1;
    if (markerCanvas.width !== timelineWidth * dpr || markerCanvas.height !== timelineHeight * dpr) {
      markerCanvas.width = timelineWidth * dpr;
      markerCanvas.height = timelineHeight * dpr;
    }
    const ctx = markerCanvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, timelineWidth, timelineHeight);
    const frac = hoverFraction ?? (duration ? (mediaEl?.currentTime ?? 0) / duration : 0);
    const x = frac * timelineWidth;
    ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--ink') || '#222';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, timelineHeight);
    ctx.stroke();
  }

  let rafId = 0;
  function markerLoop() {
    drawMarker();
    rafId = requestAnimationFrame(markerLoop);
  }

  $effect(() => {
    drawBands();
  });

  $effect(() => {
    if (!timelineWrap) return;
    const ro = new ResizeObserver((entries) => {
      timelineWidth = entries[0].contentRect.width;
    });
    ro.observe(timelineWrap);
    return () => ro.disconnect();
  });

  $effect(() => {
    markerLoop();
    return () => cancelAnimationFrame(rafId);
  });

  function fractionFromEvent(e: PointerEvent): number {
    if (!timelineWrap) return 0;
    const rect = timelineWrap.getBoundingClientRect();
    return Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
  }

  function onPointerDown(e: PointerEvent) {
    dragging = true;
    (e.target as Element).setPointerCapture(e.pointerId);
    const frac = fractionFromEvent(e);
    hoverFraction = frac;
    playback.seek(frac * duration);
  }

  function onPointerMove(e: PointerEvent) {
    if (!dragging) return;
    const frac = fractionFromEvent(e);
    hoverFraction = frac;
    playback.seek(frac * duration);
  }

  function onPointerUp() {
    dragging = false;
    hoverFraction = null;
  }
</script>

<svelte:window bind:innerWidth={windowWidth} />

<div class="player">
  <div class="media-slot" class:has-video={showVideo}>
    {#if showVideo}
      <!-- svelte-ignore a11y_media_has_caption -- the transcript itself is the caption track -->
      <video bind:this={mediaEl} src={mediaOriginalUrl(id)} preload="metadata" playsinline controls={false}
      ></video>
    {:else}
      <audio bind:this={mediaEl} preload="metadata"></audio>
    {/if}
  </div>

  <div
    class="timeline"
    bind:this={timelineWrap}
    onpointerdown={onPointerDown}
    onpointermove={onPointerMove}
    onpointerup={onPointerUp}
    onpointercancel={onPointerUp}
    role="slider"
    aria-label="Playback position"
    aria-valuemin="0"
    aria-valuemax={duration}
    aria-valuenow={playback.currentTime}
    tabindex="0"
  >
    <canvas bind:this={bandsCanvas} class="bands"></canvas>
    <canvas bind:this={markerCanvas} class="marker"></canvas>
  </div>

  <div class="controls">
    <button type="button" class="btn-icon" onclick={() => playback.playPause()} aria-label={playback.isPlaying ? 'Pause' : 'Play'}>
      {#if playback.isPlaying}
        <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><rect x="3" y="2" width="4" height="12" /><rect x="9" y="2" width="4" height="12" /></svg>
      {:else}
        <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M4 2l10 6-10 6z" /></svg>
      {/if}
    </button>

    {#if !narrow}
      <button type="button" class="btn-icon skip" onclick={() => playback.skip(-15)} aria-label="Back 15 seconds">−15</button>
    {/if}
    <button type="button" class="btn-icon skip" onclick={() => playback.skip(-5)} aria-label="Back 5 seconds">−5</button>
    <button type="button" class="btn-icon skip" onclick={() => playback.skip(5)} aria-label="Forward 5 seconds">+5</button>
    {#if !narrow}
      <button type="button" class="btn-icon skip" onclick={() => playback.skip(15)} aria-label="Forward 15 seconds">+15</button>
    {/if}

    <span class="time">{formatClock(playback.currentTime)} / {formatClock(duration)}</span>

    <button type="button" class="btn rate" onclick={() => playback.cycleRate()}>{playback.rate}×</button>

    {#if playableOriginal}
      <button type="button" class="btn" class:is-active={showVideo} onclick={toggleVideo}>Video</button>
    {/if}

    <div class="cache-indicator">
      <button type="button" class="btn-quiet cache-btn" onclick={() => (cacheMenuOpen = !cacheMenuOpen)}>
        {#if cacheStatus === 'cached'}Cached
        {:else if cacheStatus === 'caching'}Caching {Math.round(cacheProgress * 100)}%
        {:else if cacheStatus === 'unavailable'}—
        {:else}Streaming{/if}
      </button>
      {#if cacheMenuOpen}
        <div class="cache-menu">
          <button type="button" class="btn-quiet" onclick={onClearCache}>Clear cache</button>
        </div>
      {/if}
    </div>
  </div>
</div>

<style>
  .player {
    position: sticky;
    bottom: 0;
    background: var(--surface);
    border-top: 1px solid var(--border);
    padding: 8px 14px calc(8px + var(--safe-bottom));
    z-index: 20;
  }

  .media-slot.has-video {
    max-width: 100%;
    margin: 0 auto 8px;
  }

  .media-slot.has-video video {
    width: 100%;
    max-height: 40vh;
    display: block;
    background: #000;
    border-radius: var(--radius-s);
  }

  .timeline {
    position: relative;
    height: 36px;
    border-radius: var(--radius-s);
    background: var(--accent-wash);
    cursor: pointer;
    touch-action: none;
    margin-bottom: 6px;
  }

  .timeline canvas {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
  }

  .controls {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
  }

  .skip {
    font-size: 0.72rem;
    width: auto;
    min-width: 40px;
    padding: 0 6px;
  }

  .time {
    font-variant-numeric: tabular-nums;
    font-size: 0.82rem;
    color: var(--ink-muted);
    margin: 0 4px;
    white-space: nowrap;
  }

  .rate {
    font-variant-numeric: tabular-nums;
    min-width: 52px;
  }

  .is-active {
    border-color: var(--accent);
    color: var(--accent);
    background: var(--accent-wash);
  }

  .cache-indicator {
    position: relative;
    margin-left: auto;
  }

  .cache-btn {
    font-size: 0.76rem;
    color: var(--ink-faint);
    min-height: 32px;
  }

  .cache-menu {
    position: absolute;
    right: 0;
    bottom: calc(100% + 6px);
    background: var(--surface-raised);
    border: 1px solid var(--border);
    border-radius: var(--radius-s);
    box-shadow: var(--shadow-soft);
    padding: 4px;
  }

  @media (max-width: 420px) {
    .time {
      order: 10;
      width: 100%;
      text-align: center;
      margin-top: 4px;
    }
  }
</style>
