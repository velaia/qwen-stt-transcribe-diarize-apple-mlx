<script lang="ts">
  import {
    cancel as cancelJob,
    deleteTranscript,
    getOptions,
    getResult,
    getTranscript,
    patchTranscript,
    rerun,
    resmooth
  } from '../lib/api';
  import { eventsStore } from '../lib/events.svelte';
  import { router } from '../lib/router.svelte';
  import { Playback } from '../lib/playback.svelte';
  import { formatDuration } from '../lib/format';
  import Player from '../components/Player.svelte';
  import SpeakerLegend from '../components/SpeakerLegend.svelte';
  import TranscriptBody from '../components/TranscriptBody.svelte';
  import FilteredList from '../components/FilteredList.svelte';
  import DownloadMenu from '../components/DownloadMenu.svelte';
  import TagInput from '../components/TagInput.svelte';
  import OptionsForm from '../components/OptionsForm.svelte';
  import ConfirmDialog from '../components/ConfirmDialog.svelte';
  import type { Detail, OptionsResponse, Result, WebOptions } from '../lib/types';

  interface Props {
    id: string;
    initialTime: number | null;
  }

  let { id, initialTime }: Props = $props();

  let detail = $state<Detail | null>(null);
  let result = $state<Result | null>(null);
  let loading = $state(true);
  let loadError = $state<string | null>(null);
  let optionsResp = $state<OptionsResponse | null>(null);

  const playback = new Playback();
  let focusSignal = $state(0);
  let didSeekInitial = false;

  let titleDraft = $state('');
  let notesDraft = $state('');
  let notesOpen = $state(false);
  let notesTimer = 0;

  let rerunOpen = $state(false);
  let rerunOptions = $state<WebOptions | null>(null);
  let rerunDialogEl: HTMLDialogElement | undefined = $state();

  let smoothOpen = $state(false);
  let smoothDraft = $state<{ max_words: number; max_dur: number; max_gap: number } | null>(null);
  let smoothBusy = $state(false);
  let smoothDialogEl: HTMLDialogElement | undefined = $state();

  $effect(() => {
    if (!rerunDialogEl) return;
    if (rerunOpen && !rerunDialogEl.open) rerunDialogEl.showModal();
    if (!rerunOpen && rerunDialogEl.open) rerunDialogEl.close();
  });

  $effect(() => {
    if (!smoothDialogEl) return;
    if (smoothOpen && !smoothDialogEl.open) smoothDialogEl.showModal();
    if (!smoothOpen && smoothDialogEl.open) smoothDialogEl.close();
  });

  let pendingDelete = $state(false);

  async function load() {
    loading = true;
    loadError = null;
    try {
      const d = await getTranscript(id);
      detail = d;
      titleDraft = d.title;
      notesDraft = d.notes;
      eventsStore.seed(id, d.status, d.stage, d.progress, d.error);
      if (d.has_result) {
        try {
          result = await getResult(id);
        } catch {
          result = null;
        }
      } else {
        result = null;
      }
    } catch (e) {
      loadError = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    load();
    getOptions()
      .then((r) => (optionsResp = r))
      .catch(() => {});
    const off = eventsStore.onTranscriptEvent((evt) => {
      if (evt.id !== id) return;
      if (evt.action === 'deleted') {
        router.goLibrary();
        return;
      }
      load();
    });
    return off;
  });

  const liveJob = $derived(eventsStore.jobFor(id));
  const status = $derived(liveJob?.status ?? detail?.status ?? 'queued');
  const stage = $derived(liveJob?.stage ?? detail?.stage ?? null);
  const progress = $derived(liveJob?.progress ?? detail?.progress ?? null);
  const jobError = $derived(liveJob?.error ?? detail?.error ?? null);
  const isBusy = $derived(status === 'queued' || status === 'processing');

  const speakers = $derived(result?.speakers ?? detail?.speakers ?? []);
  const speechRatio = $derived.by(() => {
    const v = detail?.stats?.speech_ratio;
    return typeof v === 'number' ? v : null;
  });

  $effect(() => {
    if (didSeekInitial) return;
    if (initialTime == null) return;
    if (playback.duration > 0) {
      didSeekInitial = true;
      playback.seek(initialTime);
    }
  });

  // --- Header edits ---------------------------------------------------------
  function saveTitle() {
    if (!detail) return;
    const t = titleDraft.trim();
    if (!t || t === detail.title) {
      titleDraft = detail.title;
      return;
    }
    patchTranscript(id, { title: t }).then((d) => (detail = d));
  }

  function onTitleKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter') (e.target as HTMLElement).blur();
    else if (e.key === 'Escape' && detail) titleDraft = detail.title;
  }

  function onTagsChange(tags: string[]) {
    patchTranscript(id, { tags }).then((d) => (detail = d));
  }

  function onNotesInput() {
    clearTimeout(notesTimer);
    notesTimer = window.setTimeout(() => {
      patchTranscript(id, { notes: notesDraft }).then((d) => (detail = d));
    }, 600);
  }

  function onRenameSpeaker(label: string, name: string) {
    if (!detail) return;
    const next = { ...detail.speaker_names, [label]: name };
    patchTranscript(id, { speaker_names: next }).then((d) => (detail = d));
  }

  // --- Re-run -----------------------------------------------------------
  function openRerun() {
    if (!detail) return;
    rerunOptions = { ...detail.options };
    rerunOpen = true;
  }

  function confirmRerun() {
    if (!rerunOptions) return;
    rerun(id, rerunOptions).then(() => {
      rerunOpen = false;
      load();
    });
  }

  // --- Resmooth -----------------------------------------------------------
  function openSmooth() {
    if (!detail) return;
    smoothDraft = { ...detail.smoothing };
    smoothOpen = true;
  }

  async function applySmooth() {
    if (!smoothDraft) return;
    smoothBusy = true;
    try {
      const d = await resmooth(id, smoothDraft);
      detail = d;
      result = await getResult(id);
      smoothOpen = false;
    } finally {
      smoothBusy = false;
    }
  }

  // --- Delete ---------------------------------------------------------------
  async function confirmDelete() {
    pendingDelete = false;
    await deleteTranscript(id);
    router.goLibrary();
  }

  async function onCancelJob() {
    await cancelJob(id);
    load();
  }

  // --- Keyboard shortcuts ---------------------------------------------------
  function isTyping(target: EventTarget | null): boolean {
    const el = target as HTMLElement | null;
    if (!el) return false;
    const tag = el.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || el.isContentEditable;
  }

  function onKeydown(e: KeyboardEvent) {
    if (isTyping(e.target)) return;
    if (e.key === '/') {
      e.preventDefault();
      focusSignal++;
    } else if (e.key === ' ') {
      e.preventDefault();
      playback.playPause();
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      playback.skip(e.shiftKey ? -15 : -5);
    } else if (e.key === 'ArrowRight') {
      e.preventDefault();
      playback.skip(e.shiftKey ? 15 : 5);
    }
  }

  function seekTo(t: number) {
    playback.seek(t, { play: true });
  }
</script>

<svelte:window onkeydown={onKeydown} />

<div class="transcript-page">
  <header class="page-header">
    <button type="button" class="back-btn" onclick={() => router.goLibrary()} aria-label="Back to library">
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M10 3L5 8l5 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
    </button>

    {#if detail}
      <div class="header-main">
        <input
          class="title-input"
          type="text"
          bind:value={titleDraft}
          onblur={saveTitle}
          onkeydown={onTitleKeydown}
          aria-label="Transcript title"
        />
        <div class="meta-line">
          {formatDuration(detail.duration)} · {detail.language ?? '—'} · {speakers.length} speaker{speakers.length === 1 ? '' : 's'}
          {#if speechRatio != null} · {Math.round(speechRatio * 100)}% speech{/if}
        </div>
        <TagInput tags={detail.tags} onchange={onTagsChange} />
      </div>

      <div class="header-actions">
        {#if detail.has_result}<DownloadMenu id={detail.id} />{/if}
        <button type="button" class="btn" onclick={openRerun} disabled={isBusy}>Re-run…</button>
        {#if detail.has_result}<button type="button" class="btn" onclick={openSmooth}>Smoothing…</button>{/if}
        <button type="button" class="btn-icon btn-danger" aria-label="Delete transcript" onclick={() => (pendingDelete = true)}>
          <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path d="M3 4h10M6.5 4V2.5h3V4M4.5 4l.6 9.4a1 1 0 001 .9h3.8a1 1 0 001-.9L11.5 4" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </button>
      </div>
    {/if}
  </header>

  {#if detail}
    <details class="notes" bind:open={notesOpen}>
      <summary>Notes</summary>
      <textarea bind:value={notesDraft} oninput={onNotesInput} placeholder="Add notes…" rows="4"></textarea>
    </details>
  {/if}

  {#if loading && !detail}
    <p class="status-msg">Loading…</p>
  {:else if loadError}
    <p class="status-msg error">{loadError}</p>
  {:else if detail}
    {#if isBusy}
      <div class="progress-banner">
        <div class="progress-info">
          <span>{stage ?? status}{progress != null ? ` · ${Math.round(progress * 100)}%` : '…'}</span>
          <button type="button" class="btn btn-quiet" onclick={onCancelJob}>Cancel</button>
        </div>
        <div class="progress-bar"><div class="progress-fill" style={`width:${Math.round((progress ?? 0) * 100)}%`}></div></div>
        {#if detail.has_result}<p class="hint">Showing the previous result below while this run finishes.</p>{/if}
      </div>
    {:else if status === 'error'}
      <p class="status-msg error">{jobError ?? 'Transcription failed.'}</p>
    {/if}

    {#if speakers.length > 0}
      <div class="legend-row">
        <SpeakerLegend {speakers} speakerNames={detail.speaker_names} onrename={onRenameSpeaker} />
      </div>
    {/if}

    {#if result}
      <TranscriptBody
        segments={result.segments}
        {speakers}
        speakerNames={detail.speaker_names}
        {playback}
        {focusSignal}
      />
      <div class="filtered-wrap">
        <FilteredList rejected={result.rejected} onseek={seekTo} />
      </div>
    {:else if !isBusy}
      <p class="status-msg">No transcript yet.</p>
    {/if}
  {/if}
</div>

{#if detail}
  <Player
    id={detail.id}
    audioVersion={detail.audio_version}
    audioReady={detail.audio_ready}
    playableOriginal={detail.playable_original}
    duration={detail.duration ?? result?.duration_sec ?? 0}
    {speakers}
    turns={result?.segments ?? []}
    {playback}
  />
{/if}

<dialog bind:this={rerunDialogEl} class="rerun-dialog" onclose={() => (rerunOpen = false)}>
  {#if rerunOptions && optionsResp}
    <div class="rerun-body">
      <h2>Re-run transcription</h2>
      <p class="hint">Speaker names will be reset — labels can change between runs.</p>
      <OptionsForm
        bind:value={rerunOptions}
        {optionsResp}
        disabledHint={optionsResp.diarization_available ? null : 'Diarization is not available on this server.'}
      />
      <div class="actions">
        <button type="button" class="btn" onclick={() => (rerunOpen = false)}>Cancel</button>
        <button type="button" class="btn btn-primary" onclick={confirmRerun}>Re-run</button>
      </div>
    </div>
  {/if}
</dialog>

<dialog bind:this={smoothDialogEl} class="rerun-dialog" onclose={() => (smoothOpen = false)}>
  {#if smoothDraft}
    <div class="rerun-body">
      <h2>Speaker smoothing</h2>
      <p class="hint">Rebuilds the transcript from the raw diarized result in under a second. Speaker names are kept.</p>
      <div class="field">
        <label for="sm-w">Max words: {smoothDraft.max_words}</label>
        <input id="sm-w" type="range" min="0" max="20" step="1" bind:value={smoothDraft.max_words} />
      </div>
      <div class="field">
        <label for="sm-d">Max duration: {smoothDraft.max_dur.toFixed(1)}s</label>
        <input id="sm-d" type="range" min="0" max="10" step="0.1" bind:value={smoothDraft.max_dur} />
      </div>
      <div class="field">
        <label for="sm-g">Max gap: {smoothDraft.max_gap.toFixed(1)}s</label>
        <input id="sm-g" type="range" min="0" max="30" step="0.5" bind:value={smoothDraft.max_gap} />
      </div>
      <div class="actions">
        <button type="button" class="btn" onclick={() => (smoothOpen = false)} disabled={smoothBusy}>Cancel</button>
        <button type="button" class="btn btn-primary" onclick={applySmooth} disabled={smoothBusy}>
          {smoothBusy ? 'Applying…' : 'Apply'}
        </button>
      </div>
    </div>
  {/if}
</dialog>

<ConfirmDialog
  open={pendingDelete}
  title="Delete transcript?"
  message={detail ? `"${detail.title}" and its audio will be permanently deleted.` : ''}
  confirmLabel="Delete"
  danger
  onconfirm={confirmDelete}
  oncancel={() => (pendingDelete = false)}
/>

<style>
  .transcript-page {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-height: 0;
    padding-bottom: 40px;
  }

  .page-header {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    padding: 16px 20px 12px;
    border-bottom: 1px solid var(--border);
    position: sticky;
    top: 0;
    background: var(--bg);
    z-index: 25;
  }

  .back-btn {
    flex: none;
    width: 36px;
    height: 36px;
    border-radius: 50%;
    border: none;
    background: transparent;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .back-btn:hover {
    background: var(--accent-wash);
  }

  .header-main {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .title-input {
    font-family: var(--font-serif);
    font-size: 1.3rem;
    border: none;
    background: transparent;
    padding: 2px 0;
    width: 100%;
    max-width: var(--measure);
  }

  .title-input:hover,
  .title-input:focus {
    background: var(--accent-wash);
    border-radius: var(--radius-s);
  }

  .meta-line {
    font-size: 0.82rem;
    color: var(--ink-muted);
  }

  .header-actions {
    display: flex;
    align-items: center;
    gap: 6px;
    flex: none;
    flex-wrap: wrap;
    justify-content: flex-end;
  }

  .notes {
    padding: 0 20px;
    margin-top: 10px;
  }

  .notes summary {
    cursor: pointer;
    font-size: 0.85rem;
    color: var(--ink-muted);
    list-style: none;
  }

  .notes summary::before {
    content: '▸ ';
    font-size: 0.7em;
  }

  .notes[open] summary::before {
    content: '▾ ';
  }

  .notes textarea {
    width: 100%;
    max-width: var(--measure);
    margin-top: 8px;
    padding: 10px;
    border-radius: var(--radius-s);
    resize: vertical;
    font-family: var(--font-sans);
    font-size: 0.9rem;
  }

  .status-msg {
    padding: 40px 20px;
    text-align: center;
    color: var(--ink-muted);
  }

  .status-msg.error {
    color: var(--danger);
  }

  .progress-banner {
    margin: 16px 20px;
    padding: 12px 16px;
    background: var(--warn-wash);
    border-radius: var(--radius-m);
  }

  .progress-info {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 0.88rem;
  }

  .progress-bar {
    height: 4px;
    background: var(--border);
    border-radius: 2px;
    overflow: hidden;
    margin-top: 8px;
  }

  .progress-fill {
    height: 100%;
    background: var(--warn);
    transition: width 300ms ease;
  }

  .progress-banner .hint {
    margin-top: 6px;
    font-size: 0.78rem;
    color: var(--ink-muted);
  }

  .legend-row {
    padding: 12px 20px 0;
    max-width: var(--measure);
    margin: 0 auto;
    width: 100%;
  }

  .filtered-wrap {
    max-width: var(--measure);
    margin: 0 auto;
    padding: 0 20px;
  }

  .rerun-dialog {
    max-width: min(520px, calc(100vw - 32px));
    width: 100%;
  }

  .rerun-body {
    padding: 22px 24px;
    display: flex;
    flex-direction: column;
    gap: 16px;
    max-height: 80vh;
    overflow-y: auto;
  }

  .rerun-body h2 {
    font-size: 1.05rem;
    font-weight: 600;
  }

  .rerun-body .hint {
    font-size: 0.85rem;
    color: var(--ink-muted);
  }

  .rerun-body input[type='range'] {
    width: 100%;
    accent-color: var(--accent);
  }

  .actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding-top: 8px;
    border-top: 1px solid var(--border);
  }

  @media (max-width: 640px) {
    .page-header {
      flex-wrap: wrap;
    }

    .header-actions {
      width: 100%;
      justify-content: flex-start;
      padding-left: 48px;
    }
  }
</style>
