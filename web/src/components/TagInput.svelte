<script lang="ts">
  interface Props {
    tags: string[];
    onchange: (tags: string[]) => void;
    placeholder?: string;
  }

  let { tags, onchange, placeholder = 'Add tag…' }: Props = $props();
  let draft = $state('');
  let inputEl: HTMLInputElement | undefined = $state();

  function normalize(raw: string): string {
    return raw.trim().toLowerCase().slice(0, 32);
  }

  function commit() {
    const tag = normalize(draft);
    draft = '';
    if (!tag) return;
    if (tags.includes(tag)) return;
    onchange([...tags, tag]);
  }

  function remove(tag: string) {
    onchange(tags.filter((t) => t !== tag));
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      commit();
    } else if (e.key === 'Backspace' && draft === '' && tags.length > 0) {
      remove(tags[tags.length - 1]);
    }
  }
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -- convenience click-to-focus; the input itself is the real, fully keyboard-accessible control -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="tag-input" onclick={() => inputEl?.focus()}>
  {#each tags as tag (tag)}
    <span class="chip">
      {tag}
      <button type="button" class="remove" aria-label={`Remove tag ${tag}`} onclick={() => remove(tag)}>×</button>
    </span>
  {/each}
  <input
    bind:this={inputEl}
    type="text"
    bind:value={draft}
    {placeholder}
    onkeydown={onKeydown}
    onblur={commit}
  />
</div>

<style>
  .tag-input {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    padding: 6px 8px;
    border: 1px solid var(--border);
    border-radius: var(--radius-s);
    background: var(--surface);
    cursor: text;
  }

  .tag-input:focus-within {
    border-color: var(--accent);
  }

  input {
    border: none;
    outline: none;
    background: transparent;
    flex: 1;
    min-width: 80px;
    padding: 4px 2px;
  }

  .remove {
    border: none;
    background: transparent;
    cursor: pointer;
    padding: 0;
    font-size: 0.95rem;
    line-height: 1;
    color: var(--ink-muted);
  }

  .remove:hover {
    color: var(--danger);
  }
</style>
