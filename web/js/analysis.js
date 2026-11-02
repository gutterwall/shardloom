// Music analysis: band levels, intensity, onsets, tempo and beats. A port of analysis.py.
// Curves are sampled every HOP samples and normalised to 0..1 against the song itself (5th to 95th percentile).

import { pyround } from "./rand.js";

export const RATE = 22050;
export const FRAME = 2048;
export const ONSET_FRAME = 1024;
export const HOP = 512;
export const BANDS = { bass: [20, 150], mid: [150, 2000], high: [2000, 10000] };

// ---- numpy-style helpers ---------------------------------------------------

export function hanning(m) {
  if (m === 1) return new Float64Array([1]);
  const w = new Float64Array(m);
  for (let n = 0; n < m; n++) w[n] = 0.5 - 0.5 * Math.cos((2 * Math.PI * n) / (m - 1));
  return w;
}

export function percentile(x, q) {
  const s = Float64Array.from(x).sort();
  const pos = ((s.length - 1) * q) / 100;
  const lo = Math.floor(pos), hi = Math.min(lo + 1, s.length - 1);
  return s[lo] + (s[hi] - s[lo]) * (pos - lo);
}

function normalise(x) {
  const lo = percentile(x, 5), hi = percentile(x, 95);
  const out = new Float64Array(x.length);
  if (hi - lo < 1e-9) return out;
  for (let i = 0; i < x.length; i++) out[i] = Math.min(1, Math.max(0, (x[i] - lo) / (hi - lo)));
  return out;
}

export function smooth(x, frames) {
  if (frames <= 1) return Float64Array.from(x);
  const kernel = hanning(frames + 2).slice(1, -1);
  let sum = 0;
  for (const v of kernel) sum += v;
  for (let i = 0; i < kernel.length; i++) kernel[i] /= sum;
  const pad = Math.floor(frames / 2);
  const n = x.length;
  const at = j => x[Math.min(n - 1, Math.max(0, j - pad))];
  const out = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    let acc = 0;
    for (let k = 0; k < frames; k++) acc += at(i + k) * kernel[frames - 1 - k];
    out[i] = acc;
  }
  return out;
}

// np.convolve(a, v, mode="same") for len(a) >= len(v).
function convolveSame(a, v) {
  const n = a.length, m = v.length, shift = Math.floor((m - 1) / 2);
  const out = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    let acc = 0;
    for (let j = 0; j < m; j++) {
      const k = i + shift - j;
      if (k >= 0 && k < n) acc += v[j] * a[k];
    }
    out[i] = acc;
  }
  return out;
}

function argmax(a, from = 0, to = a.length) {
  let best = from;
  for (let i = from + 1; i < to; i++) if (a[i] > a[best]) best = i;
  return best;
}

// ---- spectra -----------------------------------------------------------------

class FFT {
  constructor(n) {
    this.n = n;
    this.rev = new Uint32Array(n);
    const bits = Math.log2(n);
    for (let i = 0; i < n; i++) {
      let r = 0;
      for (let b = 0; b < bits; b++) r |= ((i >> b) & 1) << (bits - 1 - b);
      this.rev[i] = r;
    }
    this.cos = new Float64Array(n / 2);
    this.sin = new Float64Array(n / 2);
    for (let i = 0; i < n / 2; i++) {
      this.cos[i] = Math.cos((-2 * Math.PI * i) / n);
      this.sin[i] = Math.sin((-2 * Math.PI * i) / n);
    }
    this.re = new Float64Array(n);
    this.im = new Float64Array(n);
  }

  // Magnitudes of the real input's spectrum, n/2 + 1 bins, into out.
  magnitudes(input, out) {
    const { n, rev, re, im } = this;
    for (let i = 0; i < n; i++) { re[rev[i]] = input[i]; im[rev[i]] = 0; }
    for (let size = 2; size <= n; size <<= 1) {
      const half = size >> 1, step = n / size;
      for (let start = 0; start < n; start += size) {
        for (let k = 0; k < half; k++) {
          const c = this.cos[k * step], s = this.sin[k * step];
          const a = start + k, b = a + half;
          const tr = re[b] * c - im[b] * s, ti = re[b] * s + im[b] * c;
          re[b] = re[a] - tr; im[b] = im[a] - ti;
          re[a] += tr; im[a] += ti;
        }
      }
    }
    for (let k = 0; k <= n / 2; k++) out[k] = Math.hypot(re[k], im[k]);
  }
}

// Calls each(frameIndex, magnitudes) for frames centred on sample i * HOP, as analysis.spectrogram.
function spectrogram(samples, frame, each) {
  const n = samples.length, pad = frame / 2;
  const reflect = n > frame;
  const total = Math.max(n + frame, frame);
  const padded = new Float32Array(total);
  for (let j = 0; j < total; j++) {
    const i = j - pad;
    if (i >= 0 && i < n) padded[j] = samples[i];
    else if (reflect) padded[j] = samples[i < 0 ? -i : 2 * (n - 1) - i];
  }
  const count = 1 + Math.floor((total - frame) / HOP);
  const window = hanning(frame);
  const fft = new FFT(frame);
  const buf = new Float64Array(frame), mag = new Float64Array(frame / 2 + 1);
  for (let f = 0; f < count; f++) {
    const o = f * HOP;
    for (let i = 0; i < frame; i++) buf[i] = padded[o + i] * window[i];
    fft.magnitudes(buf, mag);
    each(f, mag);
  }
  return count;
}

const rfftfreq = (n, rate) => Array.from({ length: n / 2 + 1 }, (_, k) => (k * rate) / n);

function geomspace(a, b, n) {
  const out = [];
  for (let i = 0; i < n; i++) out.push(a * (b / a) ** (i / (n - 1)));
  return out;
}

// Spectral flux in 40 log-spaced bands: how much new sound starts in each frame.
function onsetStrength(samples, rate = RATE, frame = ONSET_FRAME) {
  const freqs = rfftfreq(frame, rate);
  const edges = geomspace(30, Math.min(11000, rate / 2), 41);
  const bands = [];
  for (let b = 0; b < 40; b++) {
    const lo = edges[b], hi = edges[b + 1];
    let sel = freqs.map((f, k) => (f >= lo && f < hi ? k : -1)).filter(k => k >= 0);
    if (!sel.length) {
      let best = 0;
      for (let k = 1; k < freqs.length; k++) if (Math.abs(freqs[k] - (lo + hi) / 2) < Math.abs(freqs[best] - (lo + hi) / 2)) best = k;
      sel = [best];
    }
    bands.push(sel);
  }
  const logs = [];
  spectrogram(samples, frame, (f, mag) => {
    const row = new Float64Array(40);
    for (let b = 0; b < 40; b++) {
      let s = 0;
      for (const k of bands[b]) s += mag[k];
      row[b] = Math.log1p(1000 * (s / bands[b].length));
    }
    logs.push(row);
  });
  const flux = new Float64Array(logs.length);
  for (let t = 1; t < logs.length; t++) {
    let s = 0;
    for (let b = 0; b < 40; b++) s += Math.max(0, logs[t][b] - logs[t - 1][b]);
    flux[t] = s;
  }
  flux[0] = 0;
  if (flux.length > 1) flux[1] = 0;
  // Remove the slow trend so loud passages do not look like constant onsets.
  const trend = smooth(flux, 32);
  for (let i = 0; i < flux.length; i++) flux[i] = Math.max(0, flux[i] - trend[i]);
  return flux;
}

// Tempo (BPM) from the onset curve's autocorrelation, leaning towards prefer.
export function estimateTempo(onset, fps, low = 60, high = 200, prefer = 120) {
  const g = Array.from({ length: 7 }, (_, i) => Math.exp(-0.5 * (i - 3) ** 2));
  const x = convolveSame(onset, g);
  const n = x.length;
  let mean = 0;
  for (const v of x) mean += v;
  mean /= n || 1;
  let any = false;
  for (let i = 0; i < n; i++) { x[i] -= mean; if (x[i] !== 0) any = true; }
  if (n < 8 || !any) return prefer;
  const score = new Float64Array(n).fill(-Infinity);
  let valid = false;
  for (let lag = 1; lag < n; lag++) {
    const bpm = (60 * fps) / lag;
    if (bpm < low || bpm > high) continue;
    valid = true;
    let ac = 0;
    for (let i = 0; i + lag < n; i++) ac += x[i] * x[i + lag];
    // A gentle preference (one octave wide) resolves half/double tempo ambiguity.
    score[lag] = ac * Math.exp(-0.5 * Math.log2(bpm / prefer) ** 2);
  }
  if (!valid) return prefer;
  let best = argmax(score);
  if (best >= 1 && best < n - 1 && isFinite(score[best - 1]) && isFinite(score[best + 1])) {
    const a = score[best - 1], b = score[best], c = score[best + 1];
    const denom = a - 2 * b + c;
    if (denom < 0) best = best + (0.5 * (a - c)) / denom;
  }
  return (60 * fps) / best;
}

// Beat frame indices by dynamic programming (Ellis 2007).
export function trackBeats(onset, fps, bpm, tightness = 100) {
  const period = (60 * fps) / bpm;
  const n = onset.length;
  if (n < 2 || !onset.some(v => v !== 0)) {
    const out = [];
    for (let b = 0; b < n; b += period) out.push(b);
    return Float64Array.from(out);
  }
  let mean = 0;
  for (const v of onset) mean += v;
  mean /= n;
  let vr = 0;
  for (const v of onset) vr += (v - mean) ** 2;
  const sd = Math.sqrt(vr / n);
  const norm = Float64Array.from(onset, v => v / (sd + 1e-9));
  const P = Math.trunc(period);
  const kernel = Array.from({ length: 2 * P + 1 }, (_, i) => Math.exp(-0.5 * ((i - P) / (period / 32)) ** 2));
  const local = convolveSame(norm, kernel);
  const score = Float64Array.from(local);
  const back = new Int32Array(n).fill(-1);
  const lo = pyround(period / 2), hi = pyround(2 * period);
  const offsets = [], penalty = [];
  for (let o = lo; o <= hi; o++) { offsets.push(o); penalty.push(-tightness * Math.log(o / period) ** 2); }
  for (let t = lo; t < n; t++) {
    let best = -Infinity, from = -1;
    for (let k = 0; k < offsets.length; k++) {
      const prev = t - offsets[k];
      if (prev < 0) continue;
      const c = score[prev] + penalty[k];
      if (c > best) { best = c; from = prev; }
    }
    // Only chain to an earlier beat that helps: the first beat has none.
    if (from >= 0 && best > 0) {
      score[t] = local[t] + best;
      back[t] = from;
    }
  }
  const tail0 = Math.max(0, n - P - 1);
  const end = argmax(score, tail0, n);
  const beats = [end];
  while (back[beats[beats.length - 1]] >= 0) beats.push(back[beats[beats.length - 1]]);
  beats.reverse();
  return refine(trim(Float64Array.from(beats), local), norm);
}

// Drops weak beats at the start and end (the tracker extends the grid into silence).
function trim(beats, local) {
  if (beats.length < 3) return beats;
  const strength = Array.from(beats, b => local[Math.trunc(b)]);
  const floor = 0.5 * Math.sqrt(strength.reduce((a, s) => a + s * s, 0) / strength.length);
  const strong = strength.map((s, i) => (s >= floor ? i : -1)).filter(i => i >= 0);
  return strong.length ? beats.slice(strong[0], strong[strong.length - 1] + 1) : beats;
}

// Moves each beat onto the strongest onset within reach frames, between frames by parabolic interpolation.
function refine(beats, onset, reach = 2) {
  const out = Float64Array.from(beats);
  const n = onset.length;
  beats.forEach((b, k) => {
    const i = Math.trunc(b);
    const lo = Math.max(1, i - reach), hi = Math.min(n - 1, i + reach + 1);
    if (hi - lo < 1) return;
    const j = argmax(onset, lo, hi);
    if (onset[j] <= 0 || j <= 0 || j >= n - 1) return;
    const a = onset[j - 1], c = onset[j], d = onset[j + 1];
    const denom = a - 2 * c + d;
    out[k] = j + (denom < 0 ? (0.5 * (a - d)) / denom : 0);
  });
  return out;
}

// ---- the analysis of a song ----------------------------------------------------

export class Analysis {
  constructor(duration, fps, tempo, beats, curves = {}, key = "") {
    this.duration = duration;
    this.fps = fps;
    this.tempo = tempo;
    this.beats = Float64Array.from(beats);
    this.curves = curves;
    this.key = key;
    this.times = Float64Array.from(this.beats, b => b / fps);
  }

  static from(d) {
    return new Analysis(d.duration, d.fps, d.tempo, d.beats, d.curves, d.key || "");
  }

  toData() {
    return { duration: this.duration, fps: this.fps, tempo: this.tempo, beats: this.beats, curves: this.curves, key: this.key };
  }

  // Every curve at time t (linear interpolation), plus beat-derived values.
  at(t) {
    const pos = t * this.fps;
    const out = {};
    for (const [name, curve] of Object.entries(this.curves)) {
      if (!curve.length) { out[name] = 0; continue; }
      const i = Math.min(Math.max(Math.floor(pos), 0), curve.length - 1);
      const j = Math.min(i + 1, curve.length - 1);
      const f = Math.min(Math.max(pos - i, 0), 1);
      out[name] = curve[i] * (1 - f) + curve[j] * f;
    }
    const b = this.beatPosition(t);
    // 1 on the beat, decaying over about a tenth of a second.
    out.beat = Math.exp(-this.timeSinceBeat(t) / 0.1);
    out.beat_phase = b - Math.floor(b);
    out.bar_phase = b / 4 - Math.floor(b / 4);
    return out;
  }

  // Beats since the first beat, fractional, following the tracked beats.
  beatPosition(t) {
    const times = this.times, n = times.length;
    const period = 60 / this.tempo;
    if (n < 2) return (t - (n ? times[0] : 0)) / period;
    if (t <= times[0]) return (t - times[0]) / (times[1] - times[0]);
    if (t >= times[n - 1]) return n - 1 + (t - times[n - 1]) / (times[n - 1] - times[n - 2]);
    let lo = 0, hi = n;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (times[mid] <= t) lo = mid + 1; else hi = mid;
    }
    const i = lo - 1;
    return i + (t - times[i]) / (times[i + 1] - times[i]);
  }

  timeAtBeat(b) {
    const times = this.times, n = times.length;
    if (n < 2) return (n ? times[0] : 0) + (b * 60) / this.tempo;
    if (b <= 0) return times[0] + b * (times[1] - times[0]);
    if (b >= n - 1) return times[n - 1] + (b - (n - 1)) * (times[n - 1] - times[n - 2]);
    const i = Math.floor(b);
    return times[i] + (b - i) * (times[i + 1] - times[i]);
  }

  timeSinceBeat(t) {
    const b = this.beatPosition(t);
    return Math.max(0, t - this.timeAtBeat(Math.floor(b)));
  }

  // A copy whose beats are a fixed grid (bpm from offset seconds) instead of tracked beats.
  withGrid(bpm, offset = 0) {
    const count = Math.trunc(((this.duration - offset) * bpm) / 60) + 2;
    const beats = Array.from({ length: count }, (_, i) => (offset + (i * 60) / bpm) * this.fps);
    return new Analysis(this.duration, this.fps, bpm, beats, this.curves, this.key);
  }
}

// The analysis of mono samples at RATE; progress(0..1) is called along the way.
export function analyseSamples(samples, rate = RATE, progress = () => {}) {
  const fps = rate / HOP;
  const freqs = rfftfreq(FRAME, rate);
  const sel = Object.fromEntries(Object.entries(BANDS).map(([k, [lo, hi]]) =>
    [k, freqs.map((f, i) => (f >= lo && f < hi ? i : -1)).filter(i => i >= 0)]));
  const raw = { bass: [], mid: [], high: [] }, rms = [];
  const count = 1 + Math.floor(samples.length / HOP);
  spectrogram(samples, FRAME, (f, mag) => {
    for (const k of Object.keys(BANDS)) {
      let s = 0;
      for (const i of sel[k]) s += mag[i];
      raw[k].push(Math.log1p((s / sel[k].length) * 50));
    }
    let sq = 0;
    for (let i = 0; i < mag.length; i++) sq += mag[i] * mag[i];
    rms.push(Math.sqrt(sq / mag.length));
    if (f % 500 === 0) progress(0.6 * f / count);
  });
  const curves = {};
  for (const k of Object.keys(BANDS)) curves[k] = normalise(smooth(raw[k], 3));
  curves.level = normalise(smooth(rms.map(r => 20 * Math.log10(r + 1e-6)), 3));
  // A shorter window for onsets: a long one hears a hit before it happens.
  const onset = onsetStrength(samples).slice(0, rms.length);
  progress(0.85);
  curves.onset = normalise(onset);
  // Section intensity: loudness and activity over about two seconds.
  const busy = smooth(curves.onset, 9);
  curves.intensity = normalise(smooth(curves.level.map((v, i) => 0.6 * v + 0.4 * busy[i]), Math.trunc(2 * fps)));
  const tempo = estimateTempo(onset, fps);
  const beats = trackBeats(onset, fps, tempo);
  progress(1);
  const out = {};
  for (const [k, v] of Object.entries(curves)) out[k] = Float32Array.from(v);
  return new Analysis(samples.length / rate, fps, tempo, beats, out);
}
