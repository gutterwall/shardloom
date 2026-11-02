// Shard timing: holding each layer on one shard for a while instead of cutting on the sequencer's steps.
// As pace.py on the desktop; the settings live in project.pace.

export const LENGTHS = [0.25, 0.5, 1, 2, 4, 6, 8];
export const LABELS = ["1/4 beat", "1/2 beat", "1 beat", "2 beats", "4 beats", "6 beats", "8 beats"];

export function defaultPace() {
  return { on: false, weights: [0, 0, 1, 1, 1, 0, 0], tilt: 0, repeats: 4, intensity: 0.5 };
}

export function settings(project) {
  const out = { ...defaultPace(), ...(project.pace || {}) };
  const w = [...out.weights].slice(0, LENGTHS.length);
  while (w.length < LENGTHS.length) w.push(0);
  out.weights = w;
  return out;
}

// The chance of each length (sums to 1) at this music intensity (0..1).
export function shares(pace, intensity = 0.5) {
  // Positive leans to longer lengths: the speed setting, less when the music is intense.
  const lean = Number(pace.tilt) - Number(pace.intensity) * (2 * intensity - 1);
  // Each doubling in length is 3x more (or less) likely at full lean.
  const w = pace.weights.map((x, i) => Math.max(0, Number(x)) * 3 ** (lean * Math.log2(LENGTHS[i])));
  const total = w.reduce((a, b) => a + b, 0);
  if (total <= 0) return LENGTHS.map(n => (n === 1 ? 1 : 0));
  return w.map(x => x / total);
}

// A length in beats, for a uniform r in [0, 1).
export function pick(pace, intensity, r) {
  const s = shares(pace, intensity);
  for (let i = 0; i < LENGTHS.length; i++) {
    r -= s[i];
    if (r < 0) return LENGTHS[i];
  }
  for (let i = LENGTHS.length - 1; i >= 0; i--) if (s[i] > 0) return LENGTHS[i];
  return 1;
}
