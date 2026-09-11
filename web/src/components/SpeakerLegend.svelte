<script lang="ts">
  import { speakerDisplayName, speakerHue } from '../lib/speakerColor';

  interface Props {
    speakers: string[];
    speakerNames: Record<string, string>;
    onrename: (label: string, name: string) => void;
  }

  let { speakers, speakerNames, onrename }: Props = $props();
  let editing = $state<string | null>(null);
  let draft = $state('');
  let editInputEl: HTMLInputElement | undefined = $state();

  function startEdit(label: string) {
    editing = label;
    draft = speakerDisplayName(label, speakerNames);
  }

  $effect(() => {
    if (editing !== null) editInputEl?.focus();
  });

  function commit(label: string) {
    if (editing !== label) return;
    const name = draft.trim();
    editing = null;
    if (name && name !== speakerDisplayName(label, speakerNames)) {
      onrename(label, name);
    }
  }

  function onKeydown(e: KeyboardEvent, label: string) {
    if (e.key === 'Enter') {
      e.preventDefault();
      (e.target as HTMLInputElement).blur();
    } else if (e.key === 'Escape') {
      editing = null;
    }
  }
</script>

<ul class="legend">
  {#each speakers as label (label)}
    <li class="entry" style={`--hue:${speakerHue(label, speakers)}`}>
      <span class="swatch" aria-hidden="true"></span>
      {#if editing === label}
        <input
          bind:this={editInputEl}
          class="name-input"
          type="text"
          bind:value={draft}
          onblur={() => commit(label)}
          onkeydown={(e) => onKeydown(e, label)}
          maxlength="40"
        />
      {:else}
        <button type="button" class="name-btn" onclick={() => startEdit(label)}>
          {speakerDisplayName(label, speakerNames)}
        </button>
      {/if}
    </li>
  {/each}
</ul>

<style>
  .legend {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    list-style: none;
    margin: 0;
    padding: 0;
  }

  .entry {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 3px 4px 3px 3px;
    border-radius: 999px;
    border: 1px solid var(--border);
  }

  .swatch {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: hsl(var(--hue) var(--speaker-sat) var(--speaker-light));
    flex: none;
  }

  .name-btn {
    background: none;
    border: none;
    cursor: pointer;
    font: inherit;
    font-size: 0.82rem;
    padding: 2px 6px;
    border-radius: 999px;
    color: var(--ink);
  }

  .name-btn:hover {
    background: var(--accent-wash);
  }

  .name-input {
    font: inherit;
    font-size: 0.82rem;
    border: none;
    border-bottom: 1px solid var(--accent);
    background: transparent;
    padding: 2px 4px;
    width: 9ch;
  }
</style>
