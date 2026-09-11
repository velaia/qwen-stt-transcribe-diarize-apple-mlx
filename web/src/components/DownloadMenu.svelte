<script lang="ts">
  import { downloadUrl } from '../lib/api';
  import type { DownloadFormat } from '../lib/types';

  interface Props {
    id: string;
    label?: string;
  }

  let { id, label = 'Download' }: Props = $props();
  let open = $state(false);
  let rootEl: HTMLDivElement | undefined = $state();

  const formats: { fmt: DownloadFormat; label: string }[] = [
    { fmt: 'md', label: 'Markdown (.md)' },
    { fmt: 'txt', label: 'Plain text (.txt)' },
    { fmt: 'srt', label: 'Subtitles (.srt)' },
    { fmt: 'vtt', label: 'Subtitles (.vtt)' },
    { fmt: 'json', label: 'JSON (.json)' }
  ];

  function onWindowClick(e: MouseEvent) {
    if (open && rootEl && !rootEl.contains(e.target as Node)) open = false;
  }
</script>

<svelte:window onclick={onWindowClick} />

<div class="download-menu" bind:this={rootEl}>
  <button type="button" class="btn" onclick={() => (open = !open)} aria-haspopup="menu" aria-expanded={open}>
    {label}
    <svg width="10" height="6" viewBox="0 0 10 6" fill="none" aria-hidden="true">
      <path d="M1 1L5 5L9 1" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
    </svg>
  </button>
  {#if open}
    <ul class="menu" role="menu">
      {#each formats as f (f.fmt)}
        <li role="none">
          <a role="menuitem" href={downloadUrl(id, f.fmt)} onclick={() => (open = false)}>{f.label}</a>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  .download-menu {
    position: relative;
  }

  .menu {
    position: absolute;
    right: 0;
    top: calc(100% + 6px);
    background: var(--surface-raised);
    border: 1px solid var(--border);
    border-radius: var(--radius-s);
    box-shadow: var(--shadow-soft);
    list-style: none;
    margin: 0;
    padding: 4px;
    min-width: 170px;
    z-index: 50;
  }

  .menu a {
    display: block;
    padding: 8px 10px;
    border-radius: var(--radius-s);
    text-decoration: none;
    font-size: 0.88rem;
    color: var(--ink);
  }

  .menu a:hover {
    background: var(--accent-wash);
  }
</style>
