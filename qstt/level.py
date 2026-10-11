"""Speaker loudness leveling: make a loud host and a quiet guest sound equally loud.

A plain loudness normalization (one gain for the whole file) cannot fix this: it
moves both voices together and keeps the gap between them. What is needed is a
gain that changes over time -- a cut while the loud speaker talks, a boost
while the quiet one does -- applied smoothly enough that it is not heard.

Two ways to decide which gain applies when:

* **Speaker mode** (a JSON transcript from transcribe.py is given): each
  diarized speaker is measured once over everything they said and gets one
  gain, so their own dynamics are left alone. A small per-turn trim catches a
  speaker who leaned away from the mic for a stretch.
* **Auto mode** (no transcript): the running speech level is tracked with a
  median over a few seconds, which flips cleanly at a change of speaker
  instead of smearing across it, and the gain follows it.

In both, pauses inherit the gain of the speech around them rather than being
boosted on their own, so room tone between sentences is not pumped up. A last
pass sets the whole file to a podcast loudness target (-16 LUFS by default)
and a limiter keeps boosted peaks from clipping.

Analysis runs on a 16 kHz mono copy; the gain curve is then applied to the
original audio at full quality, streamed in chunks so long episodes fit in
memory.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from qstt.audio import SAMPLE_RATE

HOP = 0.1          # analysis step, seconds
WINDOW = 0.4       # analysis block, seconds (the EBU R128 momentary block)
SILENCE_DB = -60.0  # never treat anything quieter than this as speech


@dataclass
class LevelReport:
    mode: str
    speaker_gains_db: dict[str, float] = field(default_factory=dict)
    speaker_levels_db: dict[str, float] = field(default_factory=dict)
    offset_db: float = 0.0
    gain_range_db: tuple[float, float] = (0.0, 0.0)
    output_lufs: float | None = None


# --------------------------------------------------------------------------- analysis

# Close to the EBU R128 "K" weighting: drop the lows the ear barely hears and
# lift the presence range it hears best. Measuring plain RMS instead makes a
# deep voice look louder than it sounds and leaves it too quiet afterwards.
K_WEIGHT = "highpass=f=60:poles=2,highshelf=f=1500:g=4"


def load_weighted(path: str | Path) -> np.ndarray:
    """K-weighted 16 kHz analysis copy, shape (samples, channels), up to stereo."""
    path = Path(path)
    ch = min(2, _probe(path)[1])
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-vn", "-af", K_WEIGHT,
         "-f", "f32le", "-ac", str(ch), "-ar", str(SAMPLE_RATE), "-"],
        capture_output=True,
    )
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-5:]
        raise RuntimeError(f"ffmpeg failed on {path.name}:\n" + "\n".join(tail))
    return np.frombuffer(proc.stdout, dtype=np.float32).reshape(-1, ch)


def block_levels(audio: np.ndarray, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Loudness in LUFS of overlapping 400 ms blocks, one every 100 ms.

    Channel powers are summed as in R128, so a stereo file reads 3 dB louder
    than its mono downmix, matching what loudness meters and players report.
    """
    x = audio.reshape(len(audio), -1).astype(np.float64)
    hop, win = int(HOP * sr), int(WINDOW * sr)
    n = max(0, (len(x) - win) // hop + 1)
    if n == 0:
        return np.full(1, -120.0)
    sq = np.cumsum(np.concatenate([[0.0], (x * x).sum(axis=1)]))
    starts = np.arange(n) * hop
    power = (sq[starts + win] - sq[starts]) / win
    return -0.691 + 10 * np.log10(np.maximum(power, 1e-12))


def integrated_lufs(levels: np.ndarray) -> float:
    """R128 integrated loudness from block levels: -70 LUFS and -10 LU gates."""
    lv = levels[levels > -70]
    if lv.size == 0:
        return -70.0
    rel = 10 * np.log10(np.mean(10 ** (lv / 10))) - 10
    lv = lv[lv > rel]
    return float(10 * np.log10(np.mean(10 ** (lv / 10))))


def speech_mask(levels: np.ndarray, above_floor_db: float = 10.0,
                below_peak_db: float = 40.0) -> np.ndarray:
    """Blocks loud enough to be someone talking rather than room tone.

    The floor comes from the quietest 1% -- the gaps between words -- not from
    a higher percentile, which in an interview can already be the quiet
    speaker and would gate them out as noise.
    """
    floor = np.percentile(levels, 1)
    peak = np.percentile(levels, 95)
    return levels > max(floor + above_floor_db, peak - below_peak_db, SILENCE_DB)


def _fill_gaps(values: np.ndarray, known: np.ndarray) -> np.ndarray:
    """Linear interpolation across unknown blocks; ends hold the nearest value."""
    if not known.any():
        return np.zeros_like(values)
    idx = np.arange(len(values))
    return np.interp(idx, idx[known], values[known])


def _moving_average(x: np.ndarray, n: int) -> np.ndarray:
    if n <= 1 or len(x) < 2:
        return x
    pad = n // 2
    xp = np.pad(x, (pad, n - 1 - pad), mode="edge")
    c = np.cumsum(np.concatenate([[0.0], xp]))
    return (c[n:] - c[:-n]) / n


def auto_gain(levels: np.ndarray, speech: np.ndarray, target_db: float,
              max_boost: float, max_cut: float, track_sec: float = 3.0) -> np.ndarray:
    """Gain per block from a running median of speech level (no diarization)."""
    half = max(1, int(track_sec / HOP / 2))
    lv = np.where(speech, levels, np.nan)
    padded = np.pad(lv, half, constant_values=np.nan)
    windows = np.lib.stride_tricks.sliding_window_view(padded, 2 * half + 1)
    has = speech.copy()
    tracked = np.full(len(levels), np.nan)
    rows = np.flatnonzero(has)
    if rows.size:
        tracked[rows] = np.nanmedian(windows[rows], axis=1)
    gain = np.clip(target_db - tracked, -max_cut, max_boost)
    return _fill_gaps(np.nan_to_num(gain), has)


def speaker_gain(levels: np.ndarray, speech: np.ndarray, segments: list[dict],
                 target_db: float, max_boost: float, max_cut: float,
                 turn_trim: float, report: LevelReport) -> np.ndarray:
    """One gain per diarized speaker, plus a small trim per turn."""
    n = len(levels)
    centers = np.arange(n) * HOP + WINDOW / 2

    def blocks_of(seg):
        return (centers >= seg["start"]) & (centers < seg["end"]) & speech

    per_speaker: dict[str, list[np.ndarray]] = {}
    for seg in segments:
        per_speaker.setdefault(seg.get("speaker") or "?", []).append(blocks_of(seg))

    for spk, masks in per_speaker.items():
        m = np.logical_or.reduce(masks)
        if m.sum() < 5:
            continue
        lvl = float(np.median(levels[m]))
        report.speaker_levels_db[spk] = round(lvl, 1)
        report.speaker_gains_db[spk] = round(
            float(np.clip(target_db - lvl, -max_cut, max_boost)), 1)

    gain = np.zeros(n)
    known = np.zeros(n, dtype=bool)
    for seg in segments:
        spk = seg.get("speaker") or "?"
        if spk not in report.speaker_gains_db:
            continue
        m = blocks_of(seg)
        if not m.any():
            continue
        g = report.speaker_gains_db[spk]
        if turn_trim > 0 and m.sum() >= 10:
            turn = float(np.median(levels[m])) + g
            g += float(np.clip(target_db - turn, -turn_trim, turn_trim))
        g = float(np.clip(g, -max_cut, max_boost))
        span = (centers >= seg["start"]) & (centers < seg["end"])
        gain[span] = g
        known |= span
    return _fill_gaps(gain, known)


def load_segments(path: str | Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    segs = [s for s in data.get("segments", []) if s.get("end", 0) > s.get("start", 0)]
    if not segs:
        raise ValueError(f"{path} has no timed segments")
    return segs


def measure_lufs(path: str | Path) -> float | None:
    """Integrated loudness of a file as ffmpeg's ebur128 meter reports it."""
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path), "-vn",
         "-af", "ebur128", "-f", "null", "-"],
        capture_output=True,
    )
    found = re.findall(r"I:\s+(-?[\d.]+) LUFS", proc.stderr.decode("utf-8", "replace"))
    return float(found[-1]) if found else None


def gain_curve(levels: np.ndarray, segments: list[dict] | None = None, *,
               target_lufs: float = -16.0, max_boost: float = 18.0, max_cut: float = 18.0,
               turn_trim: float = 4.0, smooth_sec: float = 0.5,
               ) -> tuple[np.ndarray, LevelReport]:
    """Gain in dB at each analysis block (every HOP seconds)."""
    speech = speech_mask(levels)
    # Level everyone to a common speech level first; the absolute loudness is
    # set afterwards by measurement. Halfway between the quiet and the loud end
    # of the speech splits the work between cutting one speaker and boosting
    # the other, whoever talks more, so neither hits max_boost / max_cut early.
    ref = float(np.mean(np.percentile(levels[speech], [10, 90]))) if speech.any() else -20.0
    if segments:
        report = LevelReport("speaker")
        gain = speaker_gain(levels, speech, segments, ref, max_boost, max_cut,
                            turn_trim, report)
    else:
        report = LevelReport("auto")
        gain = auto_gain(levels, speech, ref, max_boost, max_cut)
    gain = _moving_average(gain, int(round(smooth_sec / HOP)))

    report.offset_db = round(target_lufs - integrated_lufs(levels + gain), 2)
    gain = gain + report.offset_db
    report.gain_range_db = (round(float(gain.min()), 1), round(float(gain.max()), 1))
    return gain, report


def db_to_linear(db: np.ndarray) -> np.ndarray:
    return (10 ** (db / 20)).astype(np.float32)


def expand(gain_db: np.ndarray, n_samples: int, sr: int, offset: int = 0) -> np.ndarray:
    """Interpolate the per-block curve to per-sample values for samples offset..offset+n."""
    t = (np.arange(n_samples) + offset) / sr
    centers = np.arange(len(gain_db)) * HOP + WINDOW / 2
    return np.interp(t, centers, gain_db).astype(np.float32)


# --------------------------------------------------------------------------- rendering

def _probe(path: Path) -> tuple[int, int]:
    """(sample_rate, channels) of the first audio stream."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate,channels", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    st = json.loads(out)["streams"][0]
    return int(st["sample_rate"]), min(2, int(st["channels"]))


def render(src: Path, dst: Path, gain_db: np.ndarray, *, bitrate: str | None = None,
           ceiling_db: float = -1.5, chunk_sec: float = 10.0) -> None:
    """Apply the gain curve to the original audio and encode to dst."""
    sr, ch = _probe(src)
    dec = subprocess.Popen(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(src), "-vn",
         "-f", "f32le", "-ac", str(ch), "-ar", str(sr), "-"],
        stdout=subprocess.PIPE,
    )
    limit = 10 ** (ceiling_db / 20)
    enc_cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y",
               "-f", "f32le", "-ac", str(ch), "-ar", str(sr), "-i", "-",
               "-af", f"alimiter=limit={limit:.4f}:level=0:attack=5:release=50"]
    if bitrate:
        enc_cmd += ["-b:a", bitrate]
    enc = subprocess.Popen(enc_cmd + [str(dst)], stdin=subprocess.PIPE)

    frame_bytes = 4 * ch
    chunk = int(chunk_sec * sr) * frame_bytes
    pos = 0
    try:
        while True:
            buf = dec.stdout.read(chunk)
            if not buf:
                break
            buf = buf[: len(buf) - len(buf) % frame_bytes]
            x = np.frombuffer(buf, dtype=np.float32).reshape(-1, ch)
            g = db_to_linear(expand(gain_db, len(x), sr, pos))
            enc.stdin.write((x * g[:, None]).astype(np.float32).tobytes())
            pos += len(x)
    finally:
        enc.stdin.close()
        dec.stdout.close()
    if dec.wait() != 0 or enc.wait() != 0:
        raise RuntimeError(f"ffmpeg failed while writing {dst}")


def level_file(src: str | Path, dst: str | Path, transcript: str | Path | None = None,
               bitrate: str | None = None, **kw) -> LevelReport:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError("ffmpeg/ffprobe not found on PATH. Install with: brew install ffmpeg")
    src, dst = Path(src), Path(dst)
    segments = load_segments(transcript) if transcript else None
    gain, report = gain_curve(block_levels(load_weighted(src)), segments, **kw)
    render(src, dst, gain, bitrate=bitrate)
    report.output_lufs = measure_lufs(dst)
    # The limiter shaves boosted peaks, which can leave the result a little
    # under target. One corrective pass is enough; it only moves the curve.
    target = kw.get("target_lufs", -16.0)
    if report.output_lufs is not None and abs(target - report.output_lufs) > 0.5:
        fix = target - report.output_lufs
        gain = gain + fix
        report.offset_db = round(report.offset_db + fix, 2)
        render(src, dst, gain, bitrate=bitrate)
        report.output_lufs = measure_lufs(dst)
    report.gain_range_db = (round(float(gain.min()), 1), round(float(gain.max()), 1))
    return report
