<script lang="ts">
  import { mapWordsToChunks } from '../lib/wordMap';
  import { speakerDisplayName, speakerHue } from '../lib/speakerColor';
  import { formatClock } from '../lib/format';
  import type { Playback } from '../lib/playback.svelte';
  import type { Segment } from '../lib/types';

  interface Props {
    segments: Segment[];
    speakers: string[];
    speakerNames: Record<string, string>;
    playback: Playback;
    focusSignal: number;
  }

  let { segments, speakers, speakerNames, playback, focusSignal }: Props = $props();

  // --- Build the render model once per transcript load -------------------
  // A flat, sorted word/segment start-time array plus the DOM elements they
  // correspond to are the only things the hot playback loop touches; nothing
  // here is Svelte state, so ticking the clock never triggers a re-render.

  interface RenderChunk {
    kind: 'gap' | 'word';
    text: string;
    start?: number;
    flatIndex?: number;
  }

  interface RenderSegment {
    segIndex: number;
    start: number;
    speaker: string | null;
    chunks: RenderChunk[];
    showInlineTs: boolean;
  }

  interface RenderBlock {
    speaker: string | null;
    headerStart: number;
    segments: RenderSegment[];
  }

  interface Model {
    blocks: RenderBlock[];
    wordStarts: Float64Array;
    segStarts: Float64Array;
  }

  function buildModel(segs: Segment[]): Model {
    const blocks: RenderBlock[] = [];
    const wordStarts: number[] = [];
    const segStarts: number[] = [];
    let flatWord = 0;

    for (let si = 0; si < segs.length; si++) {
      const seg = segs[si];
      segStarts.push(seg.start);
      const raw = mapWordsToChunks(seg.text, seg.words, seg.start, seg.end);
      const chunks: RenderChunk[] = raw.map((c) => {
        if (c.kind === 'gap') return { kind: 'gap', text: c.text };
        const flatIndex = flatWord++;
        wordStarts.push(c.start);
        return { kind: 'word', text: c.text, start: c.start, flatIndex };
      });

      const last = blocks[blocks.length - 1];
      if (last && last.speaker === seg.speaker) {
        last.segments.push({ segIndex: si, start: seg.start, speaker: seg.speaker, chunks, showInlineTs: true });
      } else {
        blocks.push({
          speaker: seg.speaker,
          headerStart: seg.start,
          segments: [
            { segIndex: si, start: seg.start, speaker: seg.speaker, chunks, showInlineTs: seg.speaker === null }
          ]
        });
      }
    }

    return { blocks, wordStarts: Float64Array.from(wordStarts), segStarts: Float64Array.from(segStarts) };
  }

  const renderStart = performance.now();
  let model = $derived(buildModel(segments));

  let containerEl: HTMLDivElement | undefined = $state();
  let wordEls: HTMLElement[] = [];
  let segEls: HTMLElement[] = [];
  let activeWordIdx = -1;
  let activeSegIdx = -1;

  $effect(() => {
    const m = model;
    if (!containerEl) return;
    wordEls = Array.from(containerEl.querySelectorAll('[data-w]')) as HTMLElement[];
    segEls = Array.from(containerEl.querySelectorAll('[data-seg]')) as HTMLElement[];
    activeWordIdx = -1;
    activeSegIdx = -1;
    const elapsed = performance.now() - renderStart;
    // eslint-disable-next-line no-console
    console.log(`[perf] transcript render: ${elapsed.toFixed(1)}ms for ${wordEls.length} words / ${m.blocks.length} blocks`);
  });

  function prefersReducedMotion(): boolean {
    return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  function findActiveIndex(starts: Float64Array, t: number): number {
    let lo = 0;
    let hi = starts.length;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (starts[mid] <= t) lo = mid + 1;
      else hi = mid;
    }
    return lo - 1;
  }

  function followSegment(el: HTMLElement | undefined) {
    el?.scrollIntoView({ block: 'center', behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
  }

  function updateHighlight(t: number) {
    const wi = findActiveIndex(model.wordStarts, t);
    if (wi !== activeWordIdx) {
      if (activeWordIdx >= 0) wordEls[activeWordIdx]?.classList.remove('active-word');
      if (wi >= 0) wordEls[wi]?.classList.add('active-word');
      activeWordIdx = wi;
    }
    const si = findActiveIndex(model.segStarts, t);
    if (si !== activeSegIdx) {
      if (activeSegIdx >= 0) segEls[activeSegIdx]?.classList.remove('active-segment');
      if (si >= 0) {
        segEls[si]?.classList.add('active-segment');
        if (following) followSegment(segEls[si]);
      }
      activeSegIdx = si;
    }
  }

  // rAF loop only while playing; a direct 'seeked' listener covers the
  // paused case (scrubbing while stopped).
  $effect(() => {
    if (!playback.isPlaying) return;
    let raf = 0;
    const loop = () => {
      const el = playback.mediaEl;
      if (el) updateHighlight(el.currentTime);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  });

  $effect(() => {
    const el = playback.mediaEl;
    if (!el) return;
    const onSeeked = () => updateHighlight(el.currentTime);
    el.addEventListener('seeked', onSeeked);
    return () => el.removeEventListener('seeked', onSeeked);
  });

  // --- Auto-follow ---------------------------------------------------------
  let following = $state(true);

  function disengage() {
    following = false;
  }

  $effect(() => {
    if (!containerEl) return;
    const scrollKeys = new Set(['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End']);
    const onKey = (e: KeyboardEvent) => {
      if (scrollKeys.has(e.key)) disengage();
    };
    containerEl.addEventListener('wheel', disengage, { passive: true });
    containerEl.addEventListener('touchstart', disengage, { passive: true });
    window.addEventListener('keydown', onKey);
    return () => {
      containerEl?.removeEventListener('wheel', disengage);
      containerEl?.removeEventListener('touchstart', disengage);
      window.removeEventListener('keydown', onKey);
    };
  });

  function resumeFollow() {
    following = true;
    if (activeSegIdx >= 0) followSegment(segEls[activeSegIdx]);
  }

  // --- Click-to-seek (delegated; avoids one listener per word span) ------
  function onContainerClick(e: MouseEvent) {
    const target = (e.target as HTMLElement).closest('[data-start]') as HTMLElement | null;
    if (!target) return;
    const start = Number(target.dataset.start);
    if (!Number.isFinite(start)) return;
    playback.seek(start, { play: target.dataset.play === '1' });
  }

  // --- In-transcript search ------------------------------------------------
  let searchOpen = $state(false);
  let query = $state('');
  let matches = $state<number[]>([]);
  let matchPos = $state(0);
  let searchInputEl: HTMLInputElement | undefined = $state();
  let lastFocusSignal = 0;

  function normalize(s: string): string {
    return s.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '');
  }

  // `prevMatches` is a plain variable, not $state: the effect below both
  // computes `matches` and needs to clear the *previous* set of DOM classes,
  // and reading+writing the same $state signal in one effect creates a
  // self-triggering update loop. Clearing off a non-reactive shadow copy
  // avoids that while `matches` itself stays reactive for the UI (n/total).
  let prevMatches: number[] = [];

  function clearMatchClasses(indices: number[]) {
    for (const idx of indices) wordEls[idx]?.classList.remove('search-match', 'search-current');
  }

  $effect(() => {
    const q = normalize(query);
    clearMatchClasses(prevMatches);
    if (!q) {
      matches = [];
      prevMatches = [];
      return;
    }
    const found: number[] = [];
    for (let i = 0; i < wordEls.length; i++) {
      if (normalize(wordEls[i].textContent ?? '').includes(q)) found.push(i);
    }
    matches = found;
    prevMatches = found;
    matchPos = 0;
    for (const idx of found) wordEls[idx]?.classList.add('search-match');
    if (found.length) wordEls[found[0]]?.classList.add('search-current');
  });

  function gotoMatch(delta: number) {
    if (!matches.length) return;
    wordEls[matches[matchPos]]?.classList.remove('search-current');
    matchPos = (matchPos + delta + matches.length) % matches.length;
    const el = wordEls[matches[matchPos]];
    el?.classList.add('search-current');
    el?.scrollIntoView({ block: 'center', behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
  }

  function seekToCurrentMatch() {
    if (!matches.length) return;
    playback.seek(model.wordStarts[matches[matchPos]]);
  }

  function closeSearch() {
    clearMatchClasses(matches);
    matches = [];
    prevMatches = [];
    query = '';
    searchOpen = false;
  }

  function onSearchKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter') {
      e.preventDefault();
      if (e.shiftKey) gotoMatch(-1);
      else seekToCurrentMatch();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      closeSearch();
    }
  }

  $effect(() => {
    if (focusSignal > lastFocusSignal) {
      lastFocusSignal = focusSignal;
      searchOpen = true;
      queueMicrotask(() => searchInputEl?.focus());
    }
  });
</script>

{#if searchOpen}
  <div class="search-bar">
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <circle cx="7" cy="7" r="5" stroke="currentColor" stroke-width="1.4" />
      <path d="M11 11L14.5 14.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
    </svg>
    <input
      bind:this={searchInputEl}
      type="text"
      bind:value={query}
      placeholder="Search transcript…"
      onkeydown={onSearchKeydown}
    />
    <span class="search-count">{matches.length ? `${matchPos + 1}/${matches.length}` : '0/0'}</span>
    <button type="button" class="btn-icon" aria-label="Previous match" onclick={() => gotoMatch(-1)} disabled={!matches.length}>‹</button>
    <button type="button" class="btn-icon" aria-label="Next match" onclick={() => gotoMatch(1)} disabled={!matches.length}>›</button>
    <button type="button" class="btn-icon" aria-label="Close search" onclick={closeSearch}>×</button>
  </div>
{/if}

<!--
  svelte-ignore a11y_click_events_have_key_events -- click is delegated at the
  container for performance (avoids one listener/closure per word span across
  tens of thousands of words). Timestamps are real, keyboard-operable
  <button>s; word spans are a mouse/touch seek shortcut layered on top of a
  transcript that is otherwise plain readable text, not the only way to seek.
-->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="transcript-body" bind:this={containerEl} onclick={onContainerClick}>
  {#each model.blocks as block, bi (bi)}
    <section class="turn" class:has-header={block.speaker !== null}>
      {#if block.speaker !== null}
        <header class="turn-header" style={`--hue:${speakerHue(block.speaker, speakers)}`}>
          <span class="speaker-swatch" aria-hidden="true"></span>
          <span class="speaker-name">{speakerDisplayName(block.speaker, speakerNames)}</span>
          <button type="button" class="ts-btn" data-start={block.headerStart}>{formatClock(block.headerStart)}</button>
        </header>
      {/if}
      {#each block.segments as seg (seg.segIndex)}
        <p class="segment" data-seg={seg.segIndex}>{#if seg.showInlineTs}<button type="button" class="ts-btn ts-inline" data-start={seg.start}>{formatClock(seg.start)}</button>{/if}{#each seg.chunks as chunk, ci (ci)}{#if chunk.kind === 'word'}<span class="word" data-w={chunk.flatIndex} data-start={chunk.start} data-play="1">{chunk.text}</span>{:else}{chunk.text}{/if}{/each}</p>
      {/each}
    </section>
  {/each}
</div>

{#if !following}
  <button type="button" class="follow-pill" onclick={resumeFollow}>Follow ↓</button>
{/if}

<style>
  .search-bar {
    position: sticky;
    top: 0;
    /* Both this and .page-header (Transcript.svelte) are sticky at top: 0
       within the same scroll container, so they'd otherwise clamp to the
       same spot. Painting the search bar above the header while it is open
       is deliberate — search is a transient, page-header content isn't
       needed while typing a query. */
    z-index: 26;
    display: flex;
    align-items: center;
    gap: 8px;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 8px 16px;
    color: var(--ink-muted);
  }

  .search-bar input {
    flex: 1;
    border: none;
    background: transparent;
    outline: none;
    font-size: 0.9rem;
    color: var(--ink);
    min-height: 32px;
  }

  .search-count {
    font-variant-numeric: tabular-nums;
    font-size: 0.8rem;
    white-space: nowrap;
  }

  .transcript-body {
    font-family: var(--font-serif);
    font-size: 1.06rem;
    line-height: 1.7;
    max-width: var(--measure);
    margin: 0 auto;
    padding: 8px 20px 140px;
  }

  .turn {
    content-visibility: auto;
    contain-intrinsic-size: auto 200px;
    padding: 14px 0;
  }

  .turn + .turn {
    border-top: 1px solid var(--border);
  }

  .turn-header {
    display: flex;
    align-items: baseline;
    gap: 8px;
    margin-bottom: 6px;
    font-family: var(--font-sans);
  }

  .speaker-swatch {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: hsl(var(--hue) var(--speaker-sat) var(--speaker-light));
    flex: none;
  }

  .speaker-name {
    font-variant: small-caps;
    letter-spacing: 0.02em;
    font-size: 0.86rem;
    font-weight: 600;
    color: hsl(var(--hue) var(--speaker-sat) var(--speaker-light));
  }

  .ts-btn {
    font-family: var(--font-sans);
    font-size: 0.76rem;
    color: var(--ink-faint);
    background: none;
    border: none;
    cursor: pointer;
    padding: 2px 4px;
    font-variant-numeric: tabular-nums;
  }

  .ts-btn:hover {
    color: var(--accent);
    text-decoration: underline;
  }

  .ts-inline {
    float: left;
    margin: 0.15em 8px 0 0;
  }

  .segment {
    margin: 0 0 0.6em;
    color: var(--ink);
  }

  .segment::after {
    content: '';
    display: table;
    clear: both;
  }

  :global(.word) {
    cursor: pointer;
    border-radius: 3px;
  }

  :global(.word:hover) {
    background: var(--accent-wash);
  }

  :global(.word.active-word) {
    background: var(--accent);
    color: var(--accent-ink);
  }

  :global(.segment.active-segment) {
    background: var(--accent-wash);
    border-radius: var(--radius-s);
    margin-left: -8px;
    margin-right: -8px;
    padding-left: 8px;
    padding-right: 8px;
  }

  :global(.word.search-match) {
    background: var(--warn-wash);
  }

  :global(.word.search-current) {
    background: var(--warn);
    color: var(--accent-ink);
  }

  .follow-pill {
    position: fixed;
    bottom: 100px;
    left: 50%;
    transform: translateX(-50%);
    background: var(--ink);
    color: var(--bg);
    border: none;
    border-radius: 999px;
    padding: 8px 16px;
    font-size: 0.82rem;
    cursor: pointer;
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.25);
    z-index: 30;
  }
</style>
