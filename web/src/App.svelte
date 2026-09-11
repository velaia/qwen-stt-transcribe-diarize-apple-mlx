<script lang="ts">
  import { router } from './lib/router.svelte';
  import { eventsStore } from './lib/events.svelte';
  import { uploadsStore } from './lib/uploads.svelte';
  import Library from './routes/Library.svelte';
  import Transcript from './routes/Transcript.svelte';

  eventsStore.connect();

  let dragging = $state(false);
  let dragDepth = 0;

  function onDragEnter(e: DragEvent) {
    if (router.route.name !== 'library') return;
    if (!e.dataTransfer?.types.includes('Files')) return;
    dragDepth++;
    dragging = true;
  }

  function onDragOver(e: DragEvent) {
    if (router.route.name !== 'library') return;
    if (!e.dataTransfer?.types.includes('Files')) return;
    e.preventDefault();
  }

  function onDragLeave() {
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) dragging = false;
  }

  function onDrop(e: DragEvent) {
    if (router.route.name !== 'library') return;
    e.preventDefault();
    dragDepth = 0;
    dragging = false;
    const files = Array.from(e.dataTransfer?.files ?? []);
    if (files.length) uploadsStore.stage(files);
  }
</script>

<svelte:window
  ondragenter={onDragEnter}
  ondragover={onDragOver}
  ondragleave={onDragLeave}
  ondrop={onDrop}
/>

<a href="#main" class="skip-link">Skip to content</a>

<main id="main">
  {#if router.route.name === 'transcript'}
    {#key router.route.id}
      <Transcript id={router.route.id} initialTime={router.route.t} />
    {/key}
  {:else}
    <Library />
  {/if}
</main>

{#if dragging}
  <div class="drop-overlay" role="presentation">
    <div class="drop-overlay-card">Drop to upload</div>
  </div>
{/if}

<style>
  main {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-height: 0;
  }

  .drop-overlay {
    position: fixed;
    inset: 0;
    background: var(--accent-wash);
    backdrop-filter: blur(1px);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 200;
    pointer-events: none;
    border: 3px dashed var(--accent);
  }

  .drop-overlay-card {
    background: var(--surface);
    border: 1px solid var(--border-strong);
    border-radius: var(--radius-m);
    padding: 20px 32px;
    font-size: 1.1rem;
    box-shadow: var(--shadow-soft);
  }
</style>
