<script lang="ts">
  import { deleteTranscript, getOptions, listTranscripts, tags as fetchTags } from '../lib/api';
  import { eventsStore } from '../lib/events.svelte';
  import { uploadsStore } from '../lib/uploads.svelte';
  import { router } from '../lib/router.svelte';
  import { formatDuration, formatRelativeDate } from '../lib/format';
  import DownloadMenu from '../components/DownloadMenu.svelte';
  import ConfirmDialog from '../components/ConfirmDialog.svelte';
  import UploadPanel from '../components/UploadPanel.svelte';
  import type { OptionsResponse, Summary, TagCount, WebOptions } from '../lib/types';

  let items = $state<Summary[]>([]);
  let loading = $state(true);
  let loadError = $state<string | null>(null);
  let query = $state('');
  let activeTag = $state<string | null>(null);
  let tagCounts = $state<TagCount[]>([]);
  let optionsResp = $state<OptionsResponse | null>(null);
  let pendingDelete = $state<Summary | null>(null);
  let fileInputEl: HTMLInputElement | undefined = $state();

  let debounceTimer = 0;

  async function refetch() {
    loading = true;
    loadError = null;
    try {
      items = await listTranscripts(query || undefined, activeTag || undefined);
    } catch (e) {
      loadError = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  async function refetchTags() {
    try {
      tagCounts = await fetchTags();
    } catch {
      // non-critical
    }
  }

  $effect(() => {
    // re-run whenever query or activeTag changes
    const q = query;
    const t = activeTag;
    clearTimeout(debounceTimer);
    debounceTimer = window.setTimeout(() => refetch(), q ? 250 : 0);
    return () => clearTimeout(debounceTimer);
  });

  $effect(() => {
    getOptions()
      .then((r) => (optionsResp = r))
      .catch(() => {});
    refetchTags();
    const off = eventsStore.onTranscriptEvent(() => {
      refetch();
      refetchTags();
    });
    return off;
  });

  function jobProgress(item: Summary) {
    return eventsStore.jobFor(item.id);
  }

  function statusOf(item: Summary) {
    const live = jobProgress(item);
    return live?.status ?? item.status;
  }

  function stageOf(item: Summary) {
    const live = jobProgress(item);
    return live?.stage ?? item.stage;
  }

  function progressOf(item: Summary) {
    const live = jobProgress(item);
    return live?.progress ?? item.progress;
  }

  function errorOf(item: Summary) {
    const live = jobProgress(item);
    return live?.error ?? item.error;
  }

  function onPickFiles(e: Event) {
    const input = e.target as HTMLInputElement;
    const files = Array.from(input.files ?? []);
    if (files.length) uploadsStore.stage(files);
    input.value = '';
  }

  function confirmUpload(options: WebOptions, title: string | undefined) {
    const files = uploadsStore.staged;
    if (files.length === 1) {
      uploadsStore.start(files[0], options, title);
    } else {
      for (const f of files) uploadsStore.start(f, options);
    }
    uploadsStore.clearStaged();
  }

  async function onDelete() {
    if (!pendingDelete) return;
    const id = pendingDelete.id;
    pendingDelete = null;
    try {
      await deleteTranscript(id);
      items = items.filter((i) => i.id !== id);
    } catch (e) {
      loadError = e instanceof Error ? e.message : String(e);
    }
  }

  // An upload's placeholder row gives way as soon as its real row is listed,
  // instead of both showing until the placeholder's timer runs out.
  const pendingUploads = $derived(
    uploadsStore.active.filter((u) => !(u.summary && items.some((i) => i.id === u.summary?.id)))
  );

  function speakerCount(item: Summary): number {
    return item.speakers?.length ?? 0;
  }
</script>

<div class="library">
  <header class="lib-header">
    <h1>Transcripts</h1>
    <div class="lib-actions">
      <button type="button" class="btn btn-primary" onclick={() => fileInputEl?.click()}>Upload</button>
      <input
        bind:this={fileInputEl}
        type="file"
        multiple
        accept="audio/*,video/*,.mp3,.wav,.m4a,.flac,.ogg,.mp4,.mov,.webm,.mkv"
        class="visually-hidden"
        onchange={onPickFiles}
      />
    </div>
  </header>

  <div class="filters">
    <div class="search-field">
      <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <circle cx="7" cy="7" r="5" stroke="currentColor" stroke-width="1.4" />
        <path d="M11 11L14.5 14.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
      </svg>
      <input type="search" placeholder="Search transcripts…" bind:value={query} aria-label="Search transcripts" />
    </div>
    {#if tagCounts.length > 0}
      <div class="tag-filters">
        {#each tagCounts as tc (tc.tag)}
          <button
            type="button"
            class="chip"
            class:is-active={activeTag === tc.tag}
            onclick={() => (activeTag = activeTag === tc.tag ? null : tc.tag)}
          >
            {tc.tag} <span class="count">{tc.count}</span>
          </button>
        {/each}
      </div>
    {/if}
  </div>

  {#if pendingUploads.length > 0}
    <ul class="rows uploading">
      {#each pendingUploads as u (u.clientId)}
        <li class="row upload-row">
          <div class="row-main">
            <span class="row-title">{u.file.name}</span>
            {#if u.status === 'uploading'}
              <div class="progress-bar"><div class="progress-fill" style={`width:${Math.round(u.progress * 100)}%`}></div></div>
            {:else if u.status === 'error'}
              <span class="row-error">{u.error}</span>
            {:else}
              <span class="row-sub">Queued</span>
            {/if}
          </div>
        </li>
      {/each}
    </ul>
  {/if}

  {#if loading && items.length === 0}
    <p class="empty">Loading…</p>
  {:else if loadError}
    <p class="empty error">{loadError}</p>
  {:else if items.length === 0 && uploadsStore.active.length === 0}
    <div class="empty">
      <p>No transcripts yet.</p>
      <p class="hint">Drop an audio or video file anywhere on this page, or use Upload.</p>
    </div>
  {:else}
    <ul class="rows">
      {#each items as item (item.id)}
        {@const status = statusOf(item)}
        {@const stage = stageOf(item)}
        {@const progress = progressOf(item)}
        {@const err = errorOf(item)}
        <li class="row">
          <button type="button" class="row-open" onclick={() => router.goTranscript(item.id)}>
            <div class="row-main">
              <span class="status-dot status-{status}" title={status}></span>
              <span class="row-title">{item.title}</span>
              {#if item.tags.length}
                <span class="row-tags">
                  {#each item.tags as t (t)}<span class="chip small">{t}</span>{/each}
                </span>
              {/if}
            </div>
            <div class="row-meta">
              {#if status === 'queued' || status === 'processing'}
                <span class="row-sub">{stage ?? status}{progress != null ? ` · ${Math.round(progress * 100)}%` : ''}</span>
              {:else if status === 'error'}
                <span class="row-error">{err ?? 'Failed'}</span>
              {:else}
                <span class="row-sub">
                  {formatDuration(item.duration)} · {item.language ?? '—'}{speakerCount(item) ? ` · ${speakerCount(item)} speaker${speakerCount(item) === 1 ? '' : 's'}` : ''}
                </span>
              {/if}
              <span class="row-date">{formatRelativeDate(item.updated_at)}</span>
            </div>
            {#if status === 'queued' || status === 'processing'}
              <div class="progress-bar thin"><div class="progress-fill" style={`width:${Math.round((progress ?? 0) * 100)}%`}></div></div>
            {/if}
          </button>
          <div class="row-actions">
            {#if item.has_result}
              <DownloadMenu id={item.id} label="" />
            {/if}
            <button type="button" class="btn-icon btn-danger" aria-label={`Delete ${item.title}`} onclick={() => (pendingDelete = item)}>
              <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path d="M3 4h10M6.5 4V2.5h3V4M4.5 4l.6 9.4a1 1 0 001 .9h3.8a1 1 0 001-.9L11.5 4" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
            </button>
          </div>
        </li>
      {/each}
    </ul>
  {/if}
</div>

{#if optionsResp}
  <UploadPanel
    files={uploadsStore.staged}
    {optionsResp}
    onconfirm={confirmUpload}
    oncancel={() => uploadsStore.clearStaged()}
    onremove={(i) => uploadsStore.removeStaged(i)}
  />
{/if}

<ConfirmDialog
  open={pendingDelete !== null}
  title="Delete transcript?"
  message={pendingDelete ? `"${pendingDelete.title}" and its audio will be permanently deleted.` : ''}
  confirmLabel="Delete"
  danger
  onconfirm={onDelete}
  oncancel={() => (pendingDelete = null)}
/>

<style>
  .library {
    flex: 1;
    display: flex;
    flex-direction: column;
    max-width: 920px;
    width: 100%;
    margin: 0 auto;
    padding: 28px 20px 60px;
  }

  .lib-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 20px;
  }

  h1 {
    font-family: var(--font-serif);
    font-size: 1.5rem;
    font-weight: 500;
  }

  .filters {
    display: flex;
    flex-direction: column;
    gap: 10px;
    margin-bottom: 18px;
  }

  .search-field {
    display: flex;
    align-items: center;
    gap: 8px;
    border: 1px solid var(--border);
    border-radius: var(--radius-s);
    padding: 8px 12px;
    color: var(--ink-faint);
  }

  .search-field input {
    border: none;
    background: transparent;
    outline: none;
    flex: 1;
    font-size: 0.92rem;
    color: var(--ink);
  }

  .tag-filters {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .tag-filters .chip {
    cursor: pointer;
  }

  .count {
    color: var(--ink-faint);
  }

  .rows {
    list-style: none;
    margin: 0;
    padding: 0;
  }

  .row {
    display: flex;
    align-items: center;
    gap: 4px;
    border-bottom: 1px solid var(--border);
  }

  .row-open {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 4px;
    text-align: left;
    background: none;
    border: none;
    cursor: pointer;
    padding: 14px 8px;
    min-height: 44px;
  }

  .row-open:hover {
    background: var(--accent-wash);
  }

  .row-main {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }

  .row-title {
    font-size: 0.98rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .row-tags {
    display: flex;
    gap: 4px;
  }

  .chip.small {
    padding: 1px 8px;
    font-size: 0.7rem;
  }

  .row-meta {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    font-size: 0.8rem;
    color: var(--ink-muted);
  }

  .row-error {
    color: var(--danger);
  }

  .row-date {
    color: var(--ink-faint);
    white-space: nowrap;
  }

  .row-actions {
    display: flex;
    align-items: center;
    gap: 2px;
    padding-right: 4px;
  }

  .progress-bar {
    height: 3px;
    background: var(--border);
    border-radius: 2px;
    overflow: hidden;
    margin-top: 2px;
  }

  .progress-bar.thin {
    height: 2px;
  }

  .progress-fill {
    height: 100%;
    background: var(--accent);
    transition: width 200ms ease;
  }

  .upload-row .row-main {
    padding: 14px 8px;
    flex-direction: column;
    align-items: stretch;
    gap: 6px;
  }

  .row-sub {
    color: var(--ink-muted);
  }

  .empty {
    padding: 60px 20px;
    text-align: center;
    color: var(--ink-muted);
  }

  .empty.error {
    color: var(--danger);
  }

  .empty .hint {
    font-size: 0.85rem;
    color: var(--ink-faint);
    margin-top: 6px;
  }
</style>
