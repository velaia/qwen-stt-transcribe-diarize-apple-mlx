<script lang="ts">
  import OptionsForm from './OptionsForm.svelte';
  import { formatBytes } from '../lib/format';
  import type { OptionsResponse, WebOptions } from '../lib/types';

  interface Props {
    files: File[];
    optionsResp: OptionsResponse;
    onconfirm: (options: WebOptions, title: string | undefined) => void;
    oncancel: () => void;
    onremove: (index: number) => void;
  }

  let { files, optionsResp, onconfirm, oncancel, onremove }: Props = $props();

  let dialogEl: HTMLDialogElement | undefined = $state();
  let options = $state<WebOptions>({ ...optionsResp.defaults });
  let title = $state('');

  $effect(() => {
    if (!dialogEl) return;
    if (files.length > 0 && !dialogEl.open) dialogEl.showModal();
    if (files.length === 0 && dialogEl.open) dialogEl.close();
  });

  function confirm() {
    onconfirm(options, files.length === 1 ? title || undefined : undefined);
  }
</script>

<dialog bind:this={dialogEl} onclose={() => oncancel()}>
  <div class="panel">
    <h2>Upload {files.length === 1 ? '1 file' : `${files.length} files`}</h2>

    <ul class="files">
      {#each files as file, i (file.name + file.size + i)}
        <li>
          <span class="fname">{file.name}</span>
          <span class="fsize">{formatBytes(file.size)}</span>
          <button type="button" class="btn-icon" aria-label={`Remove ${file.name}`} onclick={() => onremove(i)}>×</button>
        </li>
      {/each}
    </ul>

    {#if files.length > 0}
      <OptionsForm
        bind:value={options}
        {optionsResp}
        showTitle={files.length === 1}
        bind:title
        disabledHint={optionsResp.diarization_available ? null : 'Diarization is not available on this server.'}
      />
    {/if}

    <div class="actions">
      <button type="button" class="btn" onclick={oncancel}>Cancel</button>
      <button type="button" class="btn btn-primary" disabled={files.length === 0} onclick={confirm}>
        Start transcribing
      </button>
    </div>
  </div>
</dialog>

<style>
  dialog {
    max-width: min(520px, calc(100vw - 32px));
    width: 100%;
  }

  .panel {
    padding: 22px 24px;
    display: flex;
    flex-direction: column;
    gap: 16px;
    max-height: 80vh;
    overflow-y: auto;
  }

  h2 {
    font-size: 1.05rem;
    font-weight: 600;
  }

  .files {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
    max-height: 140px;
    overflow-y: auto;
  }

  .files li {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 4px 0;
    border-bottom: 1px solid var(--border);
    font-size: 0.86rem;
  }

  .fname {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .fsize {
    color: var(--ink-faint);
    font-variant-numeric: tabular-nums;
    font-size: 0.78rem;
  }

  .actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding-top: 8px;
    border-top: 1px solid var(--border);
  }
</style>
