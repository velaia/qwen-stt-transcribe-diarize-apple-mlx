"""Speaker loudness leveling."""

import json
import shutil
import subprocess

import numpy as np
import pytest

from qstt.level import (
    HOP,
    WINDOW,
    block_levels,
    gain_curve,
    integrated_lufs,
    level_file,
    load_weighted,
    speech_mask,
)

SR = 16_000


def voice(sec, f0, db, sr=SR):
    """Harmonic buzz with a syllable rhythm -- speech-like enough for level work."""
    t = np.arange(int(sec * sr)) / sr
    x = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 12))
    x = x * 0.5 * (1 + np.sin(2 * np.pi * 4 * t)) ** 2
    return x / np.sqrt(np.mean(x**2)) * 10 ** (db / 20)


def interview(turns=6, loud=-12, quiet=-32, turn=6.0, gap=0.6, sr=SR):
    """Alternating loud host / quiet guest, faint room tone underneath."""
    parts, segs, t = [], [], 0.0
    for i in range(turns):
        spk, f0, db = ("HOST", 120, loud) if i % 2 == 0 else ("GUEST", 180, quiet)
        parts += [voice(turn, f0, db, sr), np.zeros(int(gap * sr))]
        segs.append({"start": t, "end": t + turn, "speaker": spk})
        t += turn + gap
    x = np.concatenate(parts)
    x += np.random.default_rng(0).standard_normal(len(x)) * 10 ** (-70 / 20)
    return x.astype(np.float32), segs


def per_speaker(levels, segs):
    centers = np.arange(len(levels)) * HOP + WINDOW / 2
    out = {}
    for s in segs:
        m = (centers > s["start"] + 0.5) & (centers < s["end"] - 0.5)
        out.setdefault(s["speaker"], []).append(levels[m])
    return {k: integrated_lufs(np.concatenate(v)) for k, v in out.items()}


def test_quiet_speaker_is_not_gated_out_as_noise():
    """The guest is half the talk; a mid-percentile noise floor would drop them."""
    x, segs = interview()
    lv = block_levels(x)
    speech = speech_mask(lv)
    centers = np.arange(len(lv)) * HOP + WINDOW / 2
    guest = segs[1]
    inside = (centers > guest["start"] + 0.5) & (centers < guest["end"] - 0.5)
    assert speech[inside].mean() > 0.9


@pytest.mark.parametrize("use_segments", [False, True])
def test_twenty_db_gap_is_closed(use_segments):
    x, segs = interview()
    lv = block_levels(x)
    before = per_speaker(lv, segs)
    assert before["HOST"] - before["GUEST"] > 18

    gain, report = gain_curve(lv, segs if use_segments else None)
    after = per_speaker(lv + gain, segs)
    assert abs(after["HOST"] - after["GUEST"]) < 1.5
    assert report.mode == ("speaker" if use_segments else "auto")


def test_overall_loudness_lands_on_target():
    x, segs = interview()
    lv = block_levels(x)
    gain, _ = gain_curve(lv, segs, target_lufs=-19.0)
    assert integrated_lufs(lv + gain) == pytest.approx(-19.0, abs=0.1)


def test_gain_limits_are_respected():
    x, segs = interview(quiet=-50)
    lv = block_levels(x)
    gain, report = gain_curve(lv, segs, max_boost=6, max_cut=6)
    spread = report.speaker_gains_db["GUEST"] - report.speaker_gains_db["HOST"]
    assert spread <= 12.01


def test_pauses_inherit_neighbouring_gain():
    """Room tone between turns is not boosted on its own."""
    x, segs = interview()
    lv = block_levels(x)
    gain, _ = gain_curve(lv, segs)
    assert gain.max() - gain.min() < 25  # nothing near the 70 dB floor-to-speech span


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_level_file_end_to_end(tmp_path):
    x, segs = interview(sr=44_100)
    src = tmp_path / "ep.wav"
    stereo = np.stack([x, x], 1)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", "44100", "-ac", "2",
                    "-i", "-", str(src)], input=stereo.tobytes(), check=True)
    tj = tmp_path / "ep.json"
    tj.write_text(json.dumps({"segments": segs}))
    dst = tmp_path / "ep.leveled.flac"

    report = level_file(src, dst, tj, target_lufs=-16.0)

    assert report.output_lufs == pytest.approx(-16.0, abs=0.6)
    after = per_speaker(block_levels(load_weighted(dst)), segs)
    assert abs(after["HOST"] - after["GUEST"]) < 1.5
