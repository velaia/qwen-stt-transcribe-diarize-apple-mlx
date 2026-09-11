<script lang="ts">
  import { formatClock } from '../lib/format';
  import type { Rejected } from '../lib/types';

  interface Props {
    rejected: Rejected[];
    onseek: (time: number) => void;
  }

  let { rejected, onseek }: Props = $props();
</script>

{#if rejected.length > 0}
  <details class="filtered">
    <summary>Filtered segments ({rejected.length})</summary>
    <table>
      <thead>
        <tr>
          <th scope="col">Time</th>
          <th scope="col">Reason</th>
          <th scope="col">Raw text</th>
        </tr>
      </thead>
      <tbody>
        {#each rejected as r, i (i)}
          <tr>
            <td>
              <button type="button" class="time-btn" onclick={() => onseek(r.start)}>{formatClock(r.start)}</button>
            </td>
            <td class="reason">{r.reason}</td>
            <td class="raw">{r.text}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </details>
{/if}

<style>
  .filtered {
    margin: 40px 0 80px;
    padding-top: 16px;
    border-top: 1px solid var(--border);
  }

  summary {
    cursor: pointer;
    font-family: var(--font-sans);
    font-size: 0.88rem;
    color: var(--ink-muted);
    list-style: none;
  }

  summary::before {
    content: '▸ ';
    font-size: 0.7em;
  }

  .filtered[open] summary::before {
    content: '▾ ';
  }

  table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 12px;
    font-family: var(--font-sans);
    font-size: 0.82rem;
  }

  th {
    text-align: left;
    color: var(--ink-faint);
    font-weight: 500;
    padding: 6px 10px;
    border-bottom: 1px solid var(--border);
  }

  td {
    padding: 6px 10px;
    border-bottom: 1px solid var(--border);
    vertical-align: top;
    color: var(--ink-muted);
  }

  .time-btn {
    background: none;
    border: none;
    color: var(--accent);
    cursor: pointer;
    font: inherit;
    padding: 0;
    font-variant-numeric: tabular-nums;
  }

  .reason {
    font-family: ui-monospace, monospace;
    font-size: 0.78rem;
    white-space: nowrap;
  }

  .raw {
    color: var(--ink-faint);
  }
</style>
