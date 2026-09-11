<script lang="ts">
  interface Props {
    open: boolean;
    title: string;
    message: string;
    confirmLabel?: string;
    danger?: boolean;
    onconfirm: () => void;
    oncancel: () => void;
  }

  let { open, title, message, confirmLabel = 'Confirm', danger = false, onconfirm, oncancel }: Props = $props();
  let dialogEl: HTMLDialogElement | undefined = $state();

  $effect(() => {
    if (!dialogEl) return;
    if (open && !dialogEl.open) dialogEl.showModal();
    if (!open && dialogEl.open) dialogEl.close();
  });
</script>

<dialog
  bind:this={dialogEl}
  onclose={() => oncancel()}
  onclick={(e) => {
    if (e.target === dialogEl) oncancel();
  }}
>
  <div class="body">
    <h2>{title}</h2>
    <p>{message}</p>
    <div class="actions">
      <button class="btn" onclick={() => oncancel()}>Cancel</button>
      <button class={danger ? 'btn btn-primary btn-danger-solid' : 'btn btn-primary'} onclick={() => onconfirm()}>
        {confirmLabel}
      </button>
    </div>
  </div>
</dialog>

<style>
  .body {
    padding: 20px 22px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    max-width: 400px;
  }

  h2 {
    font-size: 1rem;
    font-weight: 600;
  }

  p {
    color: var(--ink-muted);
    font-size: 0.9rem;
    line-height: 1.5;
  }

  .actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 8px;
  }

  .btn-danger-solid {
    background: var(--danger);
    border-color: var(--danger);
  }
</style>
