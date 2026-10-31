"""Music analysis: band levels, intensity, onsets, tempo and beats.

All curves are sampled every HOP samples and normalised to 0..1 against the
song itself (5th to 95th percentile), so a quiet song still uses the full
range of the visuals.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

RATE = 22050
FRAME = 2048
ONSET_FRAME = 1024
HOP = 512
BANDS = {"bass": (20, 150), "mid": (150, 2000), "high": (2000, 10000)}


def _normalise(x: np.ndarray) -> np.ndarray:
    lo, hi = np.percentile(x, 5), np.percentile(x, 95)
    if hi - lo < 1e-9:
        return np.zeros_like(x)
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0)


def _smooth(x: np.ndarray, frames: int) -> np.ndarray:
    if frames <= 1:
        return x
    kernel = np.hanning(frames + 2)[1:-1]
    kernel /= kernel.sum()
    pad = frames // 2
    return np.convolve(np.pad(x, (pad, frames - 1 - pad), mode="edge"), kernel, mode="valid")


def spectrogram(samples: np.ndarray, frame: int = FRAME) -> np.ndarray:
    """Magnitude spectrogram (frames x bins); frame i is centred on sample i * HOP."""
    samples = np.pad(samples, (frame // 2, frame // 2), mode="reflect") if len(samples) > frame else np.pad(samples, (frame // 2, frame // 2))
    if len(samples) < frame:
        samples = np.pad(samples, (0, frame - len(samples)))
    count = 1 + (len(samples) - frame) // HOP
    idx = np.arange(frame)[None, :] + HOP * np.arange(count)[:, None]
    window = np.hanning(frame).astype(np.float32)
    mags = []
    # In blocks, to keep memory flat on long songs.
    for b in range(0, count, 2048):
        mags.append(np.abs(np.fft.rfft(samples[idx[b:b + 2048]] * window, axis=1)).astype(np.float32))
    return np.concatenate(mags)


def onset_strength(mag: np.ndarray, rate: int = RATE, frame: int = ONSET_FRAME) -> np.ndarray:
    """Spectral flux in 40 log-spaced bands: how much new sound starts in each frame.

    Pooling into bands first keeps the many high-frequency bins from drowning
    out a kick drum.
    """
    freqs = np.fft.rfftfreq(frame, 1.0 / rate)
    edges = np.geomspace(30.0, min(11000.0, rate / 2), 41)
    bands = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = (freqs >= lo) & (freqs < hi)
        if not sel.any():
            sel = np.zeros_like(freqs, dtype=bool)
            sel[int(np.argmin(np.abs(freqs - (lo + hi) / 2)))] = True
        bands.append(mag[:, sel].mean(axis=1))
    log = np.log1p(1000.0 * np.stack(bands, axis=1))
    flux = np.maximum(0.0, np.diff(log, axis=0)).sum(axis=1)
    # The first frames have no real predecessor.
    flux = np.concatenate([[0.0], flux])
    flux[:2] = 0.0
    # Remove the slow trend so loud passages do not look like constant onsets.
    flux = flux - _smooth(flux, 32)
    return np.maximum(flux, 0.0)


def estimate_tempo(onset: np.ndarray, fps: float, low: float = 60.0, high: float = 200.0,
                   prefer: float = 120.0) -> float:
    """Tempo (BPM) from the onset curve's autocorrelation, leaning towards prefer."""
    # Slightly smoothed: a beat period between two whole frames splits its peak.
    x = np.convolve(onset, np.exp(-0.5 * (np.arange(-3, 4) / 1.0) ** 2), mode="same")
    x = x - x.mean()
    n = len(x)
    if n < 8 or not x.any():
        return prefer
    spec = np.fft.rfft(x, 2 * n)
    ac = np.fft.irfft(spec * np.conj(spec))[:n]
    lags = np.arange(n, dtype=np.float64)
    with np.errstate(divide="ignore"):
        bpm = 60.0 * fps / lags
    valid = (bpm >= low) & (bpm <= high)
    if not valid.any():
        return prefer
    # A gentle preference (one octave wide) resolves half/double tempo ambiguity.
    weight = np.exp(-0.5 * (np.log2(np.where(valid, bpm, prefer) / prefer) / 1.0) ** 2)
    score = np.where(valid, ac * weight, -np.inf)
    best = int(np.argmax(score))
    # Parabolic interpolation for a lag between frames.
    if 1 <= best < n - 1 and np.isfinite(score[best - 1]) and np.isfinite(score[best + 1]):
        a, b, c = score[best - 1], score[best], score[best + 1]
        denom = a - 2 * b + c
        if denom < 0:
            best = best + 0.5 * (a - c) / denom
    return float(60.0 * fps / best)


def track_beats(onset: np.ndarray, fps: float, bpm: float, tightness: float = 100.0) -> np.ndarray:
    """Beat frame indices by dynamic programming (Ellis 2007)."""
    period = 60.0 * fps / bpm
    if len(onset) < 2 or not onset.any():
        return np.arange(0, len(onset), period)
    norm = onset / (onset.std() + 1e-9)
    # Local score: onsets smoothed by a beat-sized Gaussian.
    w = np.arange(-int(period), int(period) + 1)
    local = np.convolve(norm, np.exp(-0.5 * (w / (period / 32.0)) ** 2), mode="same")
    n = len(local)
    score = local.copy()
    back = np.full(n, -1, dtype=np.int64)
    lo, hi = int(round(period / 2)), int(round(2 * period))
    offsets = np.arange(lo, hi + 1)
    penalty = -tightness * np.log(offsets / period) ** 2
    for t in range(lo, n):
        prev = t - offsets
        ok = prev >= 0
        if not ok.any():
            continue
        cand = score[prev[ok]] + penalty[ok]
        k = int(np.argmax(cand))
        # Only chain to an earlier beat that helps: the first beat has none.
        if cand[k] > 0:
            score[t] = local[t] + cand[k]
            back[t] = prev[ok][k]
    # Last beat: the best score within the final beat period that has a strong local onset.
    tail = np.arange(max(0, n - int(period) - 1), n)
    end = int(tail[np.argmax(score[tail])])
    beats = [end]
    while back[beats[-1]] >= 0:
        beats.append(int(back[beats[-1]]))
    beats = np.array(beats[::-1], dtype=np.float64)
    return refine(trim(beats, local), norm)


def trim(beats: np.ndarray, local: np.ndarray) -> np.ndarray:
    """Drops weak beats at the start and end (the tracker extends the grid into silence)."""
    if len(beats) < 3:
        return beats
    strength = local[beats.astype(int)]
    floor = 0.5 * np.sqrt(np.mean(strength ** 2))
    strong = np.nonzero(strength >= floor)[0]
    return beats[strong[0]:strong[-1] + 1] if len(strong) else beats


def refine(beats: np.ndarray, onset: np.ndarray, reach: int = 2) -> np.ndarray:
    """Moves each beat onto the strongest onset within reach frames, between frames by parabolic interpolation."""
    out = beats.copy()
    n = len(onset)
    for k, b in enumerate(beats):
        i = int(b)
        lo, hi = max(1, i - reach), min(n - 1, i + reach + 1)
        if hi - lo < 1:
            continue
        j = lo + int(np.argmax(onset[lo:hi]))
        if onset[j] <= 0 or j <= 0 or j >= n - 1:
            continue
        a, c, d = onset[j - 1], onset[j], onset[j + 1]
        denom = a - 2 * c + d
        out[k] = j + (0.5 * (a - d) / denom if denom < 0 else 0.0)
    return out


@dataclass
class Analysis:
    duration: float
    fps: float
    tempo: float
    beats: np.ndarray
    curves: dict[str, np.ndarray] = field(default_factory=dict)
    source: str = ""

    # ---- sampling -------------------------------------------------------

    def at(self, t: float) -> dict[str, float]:
        """Every curve at time t (linear interpolation), plus beat-derived values."""
        pos = t * self.fps
        out = {}
        for name, curve in self.curves.items():
            if len(curve) == 0:
                out[name] = 0.0
                continue
            i = int(np.clip(np.floor(pos), 0, len(curve) - 1))
            j = min(i + 1, len(curve) - 1)
            f = float(np.clip(pos - i, 0.0, 1.0))
            out[name] = float(curve[i] * (1 - f) + curve[j] * f)
        b = self.beat_position(t)
        since = self.time_since_beat(t)
        # 1 on the beat, decaying over about a tenth of a second.
        out["beat"] = float(np.exp(-since / 0.10))
        out["beat_phase"] = b - np.floor(b)
        out["bar_phase"] = (b / 4.0) - np.floor(b / 4.0)
        return out

    def beat_times(self) -> np.ndarray:
        return self.beats / self.fps if len(self.beats) else np.array([])

    def beat_position(self, t: float) -> float:
        """Beats since the first beat, fractional, following the tracked beats."""
        times = self.beat_times()
        period = 60.0 / self.tempo
        if len(times) < 2:
            first = times[0] if len(times) else 0.0
            return (t - first) / period
        if t <= times[0]:
            return (t - times[0]) / (times[1] - times[0])
        if t >= times[-1]:
            return len(times) - 1 + (t - times[-1]) / (times[-1] - times[-2])
        i = int(np.searchsorted(times, t, side="right") - 1)
        return i + (t - times[i]) / (times[i + 1] - times[i])

    def time_at_beat(self, b: float) -> float:
        """Inverse of beat_position."""
        times = self.beat_times()
        if len(times) < 2:
            first = times[0] if len(times) else 0.0
            return first + b * 60.0 / self.tempo
        if b <= 0:
            return times[0] + b * (times[1] - times[0])
        if b >= len(times) - 1:
            return times[-1] + (b - (len(times) - 1)) * (times[-1] - times[-2])
        i = int(np.floor(b))
        return times[i] + (b - i) * (times[i + 1] - times[i])

    def time_since_beat(self, t: float) -> float:
        b = self.beat_position(t)
        return max(0.0, t - self.time_at_beat(np.floor(b)))

    def with_grid(self, bpm: float, offset: float = 0.0) -> "Analysis":
        """A copy whose beats are a fixed grid (bpm from offset seconds) instead of tracked beats."""
        count = int((self.duration - offset) * bpm / 60.0) + 2
        beats = (offset + np.arange(count) * 60.0 / bpm) * self.fps
        return Analysis(self.duration, self.fps, bpm, beats, self.curves, self.source)

    # ---- storage --------------------------------------------------------

    def save(self, path: str | os.PathLike) -> None:
        path = Path(path)
        np.savez_compressed(path.with_suffix(".npz"), beats=self.beats, **self.curves)
        meta = {"duration": self.duration, "fps": self.fps, "tempo": self.tempo, "source": self.source,
                "beats": len(self.beats), "curves": sorted(self.curves)}
        path.with_suffix(".json").write_text(json.dumps(meta, indent=1))

    @classmethod
    def load(cls, path: str | os.PathLike) -> "Analysis":
        path = Path(path)
        meta = json.loads(path.with_suffix(".json").read_text())
        with np.load(path.with_suffix(".npz")) as data:
            curves = {k: data[k] for k in meta["curves"]}
            beats = data["beats"]
        return cls(meta["duration"], meta["fps"], meta["tempo"], beats, curves, meta.get("source", ""))


def analyse_samples(samples: np.ndarray, rate: int = RATE, source: str = "") -> Analysis:
    fps = rate / HOP
    mag = spectrogram(samples)
    freqs = np.fft.rfftfreq(FRAME, 1.0 / rate)
    curves = {}
    for name, (lo, hi) in BANDS.items():
        sel = (freqs >= lo) & (freqs < hi)
        curves[name] = _normalise(_smooth(np.log1p(mag[:, sel].mean(axis=1) * 50.0), 3))
    rms = np.sqrt((mag ** 2).mean(axis=1))
    curves["level"] = _normalise(_smooth(20 * np.log10(rms + 1e-6), 3))
    # A shorter window for onsets: a long one hears a hit before it happens.
    onset = onset_strength(spectrogram(samples, ONSET_FRAME))[: len(mag)]
    curves["onset"] = _normalise(onset)
    # Section intensity: loudness and activity over about two seconds.
    curves["intensity"] = _normalise(_smooth(0.6 * curves["level"] + 0.4 * _smooth(curves["onset"], 9), int(2.0 * fps)))
    tempo = estimate_tempo(onset, fps)
    beats = track_beats(onset, fps, tempo)
    return Analysis(len(samples) / rate, fps, tempo, beats, {k: v.astype(np.float32) for k, v in curves.items()}, source)


def analyse_file(path: str | os.PathLike) -> Analysis:
    from .media import decode_audio

    return analyse_samples(decode_audio(path, RATE), RATE, str(Path(path).resolve()))
