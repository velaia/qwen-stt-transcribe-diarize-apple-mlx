<script lang="ts">
  import type { OptionsResponse, WebOptions } from '../lib/types';

  interface Props {
    value: WebOptions;
    optionsResp: OptionsResponse;
    showTitle?: boolean;
    title?: string;
    disabledHint?: string | null;
  }

  let {
    value = $bindable(),
    optionsResp,
    showTitle = false,
    title = $bindable(''),
    disabledHint = null
  }: Props = $props();

  type SpeakerMode = 'off' | 'auto' | 'count';

  function modeFor(o: WebOptions): SpeakerMode {
    if (!o.diarize) return 'off';
    return o.num_speakers == null ? 'auto' : 'count';
  }

  let speakerMode = $state(modeFor(value));

  function setMode(mode: SpeakerMode) {
    speakerMode = mode;
    if (mode === 'off') {
      value.diarize = false;
      value.num_speakers = null;
    } else if (mode === 'auto') {
      value.diarize = true;
      value.num_speakers = null;
    } else {
      value.diarize = true;
      value.num_speakers = value.num_speakers ?? 2;
    }
  }

  const speakerLimits = $derived(optionsResp.limits.num_speakers ?? [1, 20]);

  const languageOptions = $derived(
    optionsResp.languages?.length
      ? optionsResp.languages
      : [
          { value: 'auto', label: 'Auto' },
          { value: 'en', label: 'English' },
          { value: 'de', label: 'German' },
          { value: 'mixed', label: 'Mixed EN/DE' }
        ]
  );
</script>

<div class="options-form">
  {#if showTitle}
    <div class="field">
      <label for="opt-title">Title</label>
      <input id="opt-title" type="text" bind:value={title} placeholder="Untitled" />
    </div>
  {/if}

  <div class="field">
    <label for="opt-lang">Language</label>
    <select id="opt-lang" bind:value={value.language}>
      {#each languageOptions as lang (lang.value)}
        <option value={lang.value}>{lang.label}</option>
      {/each}
    </select>
  </div>

  <div class="field">
    <span id="speakers-label">Speakers</span>
    {#if disabledHint}
      <p class="hint">{disabledHint}</p>
    {/if}
    <div class="speaker-mode" role="group" aria-labelledby="speakers-label" class:is-disabled={!!disabledHint}>
      {#each [['off', 'Off'], ['auto', 'Auto'], ['count', 'Count']] as [m, label] (m)}
        <button
          type="button"
          class="chip"
          class:is-active={speakerMode === m}
          disabled={!!disabledHint}
          onclick={() => setMode(m as SpeakerMode)}
        >
          {label}
        </button>
      {/each}
      {#if speakerMode === 'count'}
        <div class="stepper">
          <button
            type="button"
            class="btn-icon"
            aria-label="Fewer speakers"
            disabled={!!disabledHint}
            onclick={() => (value.num_speakers = Math.max(speakerLimits[0], (value.num_speakers ?? 2) - 1))}
            >−</button
          >
          <span class="stepper-value">{value.num_speakers ?? 2}</span>
          <button
            type="button"
            class="btn-icon"
            aria-label="More speakers"
            disabled={!!disabledHint}
            onclick={() => (value.num_speakers = Math.min(speakerLimits[1], (value.num_speakers ?? 2) + 1))}
            >+</button
          >
        </div>
      {/if}
    </div>
  </div>

  <details class="advanced">
    <summary>Advanced</summary>
    <div class="advanced-grid">
      <label class="checkbox-field">
        <input type="checkbox" bind:checked={value.fast} />
        <span>
          Fast model
          <span class="hint">0.6B instead of 1.7B — faster, weaker on German.</span>
        </span>
      </label>

      <div class="field">
        <label for="opt-context">Context vocabulary</label>
        <input id="opt-context" type="text" bind:value={value.context} placeholder="Kubernetes, Vattenfall, Grafana…" />
        <p class="hint">Bias decoding toward domain vocabulary and proper nouns.</p>
      </div>

      <div class="field">
        <label for="opt-vad">VAD threshold: {value.vad_threshold.toFixed(2)}</label>
        <input
          id="opt-vad"
          type="range"
          min="0.1"
          max="0.95"
          step="0.01"
          bind:value={value.vad_threshold}
        />
        <p class="hint">Raise for a noisy room or background music.</p>
      </div>

      <div class="field">
        <label for="opt-minspeech">Min speech: {value.min_speech_sec.toFixed(2)}s</label>
        <input id="opt-minspeech" type="range" min="0" max="3" step="0.05" bind:value={value.min_speech_sec} />
        <p class="hint">Ignore very short bursts — coughs, chair scrapes, door clicks.</p>
      </div>

      <label class="checkbox-field">
        <input type="checkbox" bind:checked={value.aggressive_filler} />
        <span>
          Aggressive filler removal
          <span class="hint">Also drops bare "Ja." / "Okay." — off by default since those can be genuine short turns.</span>
        </span>
      </label>

      <div class="field">
        <label for="opt-foreign">Foreign-script tolerance: {value.allow_foreign_ratio.toFixed(2)}</label>
        <input
          id="opt-foreign"
          type="range"
          min="0"
          max="1"
          step="0.01"
          bind:value={value.allow_foreign_ratio}
        />
        <p class="hint">Lower discards a segment over fewer non-Latin characters. 0 is strictest.</p>
      </div>

      <div class="field">
        <label for="opt-repeats">Max repeats: {value.max_repeats}</label>
        <input id="opt-repeats" type="range" min="2" max="20" step="1" bind:value={value.max_repeats} />
        <p class="hint">Decoding loops ("ja ja ja…") longer than this are dropped.</p>
      </div>

      <fieldset class="smoothing">
        <legend>Speaker smoothing</legend>
        <div class="field">
          <label for="opt-sw">Max words: {value.smooth_max_words}</label>
          <input id="opt-sw" type="range" min="0" max="20" step="1" bind:value={value.smooth_max_words} />
        </div>
        <div class="field">
          <label for="opt-sd">Max duration: {value.smooth_max_dur.toFixed(1)}s</label>
          <input id="opt-sd" type="range" min="0" max="10" step="0.1" bind:value={value.smooth_max_dur} />
        </div>
        <div class="field">
          <label for="opt-sg">Max gap: {value.smooth_max_gap.toFixed(1)}s</label>
          <input id="opt-sg" type="range" min="0" max="30" step="0.5" bind:value={value.smooth_max_gap} />
        </div>
        <p class="hint">
          Short interruptions shorter/closer than these are absorbed back into the surrounding speaker turn.
        </p>
      </fieldset>
    </div>
  </details>
</div>

<style>
  .options-form {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .field label {
    font-weight: 500;
  }

  select {
    padding: 8px 10px;
    border-radius: var(--radius-s);
    min-height: 36px;
  }

  .speaker-mode {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px;
  }

  .speaker-mode.is-disabled {
    opacity: 0.55;
  }

  .speaker-mode .chip {
    cursor: pointer;
    border: 1px solid var(--border);
    background: var(--surface);
  }

  .speaker-mode .chip:disabled {
    cursor: not-allowed;
  }

  .stepper {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    margin-left: 8px;
  }

  .stepper-value {
    min-width: 1.6em;
    text-align: center;
    font-variant-numeric: tabular-nums;
  }

  .advanced {
    border-top: 1px solid var(--border);
    padding-top: 12px;
  }

  .advanced summary {
    cursor: pointer;
    font-weight: 500;
    color: var(--ink-muted);
    list-style: none;
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .advanced summary::before {
    content: '▸';
    font-size: 0.7em;
    transition: transform 120ms ease;
  }

  .advanced[open] summary::before {
    transform: rotate(90deg);
  }

  .advanced-grid {
    display: flex;
    flex-direction: column;
    gap: 16px;
    margin-top: 16px;
  }

  .checkbox-field {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    cursor: pointer;
  }

  .checkbox-field input {
    margin-top: 3px;
    min-width: 18px;
    min-height: 18px;
  }

  .checkbox-field .hint {
    display: block;
    margin-top: 2px;
  }

  input[type='range'] {
    width: 100%;
    accent-color: var(--accent);
  }

  fieldset.smoothing {
    border: 1px solid var(--border);
    border-radius: var(--radius-s);
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  fieldset.smoothing legend {
    padding: 0 4px;
    font-size: 0.85rem;
    color: var(--ink-muted);
  }
</style>
