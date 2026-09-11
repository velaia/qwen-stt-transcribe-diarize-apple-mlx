# qwen-stt-transcribe-diarize

English/German speech-to-text on Apple Silicon using **Qwen3-ASR**, with speaker
diarization and — the part that matters most in practice — aggressive filtering
so that silence, mumbling and background noise never turn into invented text in
a language you never spoke.

Runs entirely locally. No audio leaves the machine.

## Why the filtering layer exists

Qwen3-ASR identifies 52 languages. That is a feature until you feed it two
seconds of room tone, at which point it will confidently decide the audio is
Chinese and transcribe it as such. Measured on this machine, with a 12-second
file of nothing but faint pink noise:

| | Detected language | Output |
|---|---|---|
| Raw model | **Chinese** | `The.` |
| This tool | — | *(nothing — no speech detected)* |

And on a real file with speech separated by 8-second pauses, the raw model
invented a trailing `Oh, yeah.` that no one said. Three independent guards stop
this:

1. **Voice activity detection** runs *before* the model. Non-speech audio is
   never decoded at all, which removes the problem at its source rather than
   cleaning up after it.
2. **The language is locked to English or German before decoding.** Language
   identification is a choice *between two options*, never an open question, so
   a third language cannot be selected. When the model's own detector returns
   something outside the pair, the audio is decoded both ways and a
   function-word profile picks the winner.
3. **Output screening.** Han, Cyrillic, Arabic, Hangul, Thai, Devanagari and
   Greek characters are by construction not a transcript of English or German
   speech. A segment that is *mostly* such characters is a hallucination in
   another language and gets discarded; a stray character in otherwise good
   English is a decoding artifact and gets stripped, so a 25-second span of
   real speech is not thrown away over one glyph. Either path guarantees no
   non-Latin text in the output. Decoding loops (`ja ja ja ja …`) and subtitle
   boilerplate (`Untertitel von …`, `Thanks for watching!`) go too.

Everything discarded is reported — see [Filtered segments](#reviewing-what-was-filtered) —
so filtering is auditable rather than silent.

## Setup

```bash
./setup.sh
```

That creates `.venv`, installs everything, and checks for `ffmpeg`. Models
(~3.4 GB for the 1.7B) download from Hugging Face on first run and are cached.

Check the environment at any time:

```bash
./.venv/bin/python transcribe.py --doctor
```

Speaker labels need one extra step, because pyannote's model is gated:

1. Accept the terms at
   [pyannote/speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1)
2. `export PYANNOTE_AUTH_TOKEN=$(cat ~/.cache/huggingface/token)`

Transcription works without this; only `--diarize` needs it.

## Usage

```bash
./.venv/bin/python transcribe.py meeting.m4a
```

Language is auto-detected between English and German. To be explicit:

```bash
# Force one language - the safest option when you know what is on the tape
./.venv/bin/python transcribe.py interview.wav --lang de

# One recording that switches between the two
./.venv/bin/python transcribe.py call.mp3 --mixed

# Speaker labels, when you know how many people are in the room
./.venv/bin/python transcribe.py standup.m4a --diarize --num-speakers 4

# A whole folder, every format, into transcripts/
./.venv/bin/python transcribe.py ~/Recordings -f all -o transcripts/
```

Add `alias qstt="$PWD/.venv/bin/python $PWD/transcribe.py"` to your `~/.zshrc`
to shorten that.

### Output formats

`-f md` (default) is a readable transcript with speaker turns and a filtering
report. Also available: `txt`, `srt`, `vtt`, `json`, or `all`. `json` carries
word-level timings and every rejected segment with its reason.

## Speed

Measured on an M1 Max (64 GB), 10 minutes of podcast audio, VAD and language
detection included:

| Configuration | Wall time | vs realtime | Speedup |
|---|---|---|---|
| 1.7B (default) | 44.8s | 13.4x | 1.00x |
| 1.7B, 2 workers | 39.4s | 15.2x | 1.14x |
| 1.7B, 4 workers | 41.2s | 14.6x | 1.09x |
| 1.7B, 6 workers | 46.3s | 13.0x | **0.97x** |
| **0.6B (`--fast`)** | **19.8s** | **30.4x** | **1.85x** |
| 1.7B + speculative decoding | 42.0s | 14.3x | 0.87x |

A 2.5-hour recording takes roughly 15 minutes at the default settings.

**Parallelism does not help much here, and `--workers` is off by default for
that reason.** Autoregressive decoding is memory-bandwidth bound: each worker
holds its own copy of the weights and they all compete for the same unified
memory bus, so two workers buy 14% and six workers are *slower* than one. The
implementation is in `qstt/parallel.py` if your recordings or hardware behave
differently — run `./bench.py your-audio.mp3 --workers 1,2,4` and check before
relying on it. `--workers` also cannot be combined with `--diarize`, which needs
the whole recording at once to tell speakers apart.

Speculative decoding (drafting with the 0.6B model) was measured too and is
**slower** than plain decoding for this workload — ASR segments are short, so
draft overhead outweighs the tokens it saves. It is not wired into the CLI.

The one lever that genuinely works is model size:

| Option | Effect |
|---|---|
| `--fast` | 0.6B instead of 1.7B: 1.85x faster, higher error rate — noticeably so on German |
| `--no-timestamps` | Skips forced alignment. Faster, but no SRT/VTT and coarser speaker boundaries |

## Tuning the filters

Defaults are chosen to avoid deleting real speech. Adjust when your recordings
differ:

| Flag | When to use it |
|---|---|
| `--vad-threshold 0.7` | Noisy room, background music. Raises the bar for what counts as speech |
| `--min-speech 0.6` | Ignore very short bursts — coughs, chair scrapes, door clicks |
| `--aggressive-filler` | Also drop bare `Ja.` / `Okay.` / `Vielen Dank.` Off by default because those are often **genuine** short turns |
| `--allow-foreign-ratio 0` | Strictest: discard a segment over a single non-Latin character |
| `--context "Kubernetes Vattenfall Grafana"` | Bias decoding toward domain vocabulary and proper nouns |
| `--no-vad` | Diagnostics only. This is what lets silence become text |

### Reviewing what was filtered

Markdown and JSON output both end with the discarded segments and why:

| Time | Reason | Raw output |
|---|---|---|
| `4:12` | `foreign-script:Han` | 嗯嗯 |
| `9:03` | `repetition:word:'ja'x11` | ja ja ja ja ja ja ja ja ja ja ja |
| `31:47` | `filler-credit:'untertitel von...'` | Untertitel von Stephanie Geiges |

If real speech shows up in that table, loosen the relevant flag above.

One rejection reason deliberately does **not** exist: an early decoder stop.
When Qwen3-ASR halts generation because it detected repetition, the text it had
already produced is usually fine, and discarding the segment costs ~25 seconds
of genuine speech. Those segments are kept and counted as `truncated-kept` in
the stats; only text that fails the repetition guard on its own merits is
dropped.

## How it fits together

```
audio file
    ↓  ffmpeg → mono 16 kHz
    ↓  VAD (silero) ────────────→ silence discarded, never decoded
    ↓  language lock ───────────→ English or German, nothing else
    ↓  Qwen3-ASR per speech span
    ↓  forced alignment ────────→ word-level timings
    ↓  pyannote diarization ────→ who spoke when
    ↓  screening ───────────────→ foreign script / loops / boilerplate dropped
transcript
```

| Module | Responsibility |
|---|---|
| `qstt/audio.py` | Decode to mono 16 kHz |
| `qstt/vad.py` | Speech detection; silero with an energy-based fallback |
| `qstt/lang.py` | Language choice, constrained to English/German |
| `qstt/filters.py` | Foreign-script, repetition and boilerplate rejection |
| `qstt/pipeline.py` | Orchestration and speaker attribution |
| `qstt/parallel.py` | Process sharding for long files |
| `qstt/writers.py` | txt / md / srt / vtt / json |

| `qstt/smooth.py` | Suppresses diarization jitter |

Speaker turns are rebuilt from the **original punctuated transcript**, not by
joining aligner tokens, so commas and full stops survive diarization.

### Diarization jitter

pyannote's turn boundaries wobble by a few hundred milliseconds. Splitting a
transcript on them word by word tears single words out of a sentence:

```
SPEAKER_00: But nonetheless, if I had
SPEAKER_02: to
SPEAKER_00: pick a single best model today
```

Nobody said "to" as a turn. Short runs that interrupt one continuing speaker
(A → B → A) are absorbed back into the surrounding speaker, then consecutive
same-speaker segments merge. On a 2.5-hour podcast this cut 914 fragments to
331 real turns. A genuinely short turn *between two different speakers* (A → B
→ C) is left alone, so real one-word answers survive.

Tune with `--smooth-max-words`, `--smooth-max-dur` and `--smooth-max-gap`, or
retune an existing transcript without re-running the model:

```bash
./resmooth.py transcripts/episode.json --max-words 5 -f md,srt
```

That reprocesses a 2.5-hour transcript in under a second, since jitter is
purely a post-processing concern.

## Tests

```bash
./.venv/bin/python -m pytest tests/ -q
```

55 tests covering the guards: that genuine English and German survive, that
umlauts and ß are never mistaken for a foreign script, that bare interjections
are kept by default, and that no code path can return a language outside the
allowed pair.

## Notes and limits

- **Apple Silicon only.** MLX is the backend.
- **`--mixed` is nearly free but less predictable.** Each span is decoded
  unforced and the result kept when the model's own label is already English or
  German; only a label outside the pair costs a second decode. Still, if a
  recording is entirely one language, `--lang en` or `--lang de` is safer —
  per-span detection can flip on a short or noisy utterance.
- **Diarization quality depends on the recording.** Overlapping speech and
  shared microphones are hard; `--num-speakers` helps when you know the count.
- **The 0.6B model is noticeably weaker on German** than the 1.7B. Prefer the
  default unless you need the speed.
