// The effects: names, ranges and what they do (implemented in shaders.js). As fx.py on the desktop.
// Every effect is an amount from 0 (off) to 1 (full); step, lane, punch-in and music amounts add up, clamped.

export const FX = {
  zoom: "Push in on the picture",
  shake: "Jolt the frame around",
  mirror: "Fold left onto right",
  kaleido: "Kaleidoscope (more amount, more segments)",
  pixelate: "Big blocky pixels",
  glitch: "Displaced blocks and torn lines",
  rgb_split: "Pull the red and blue channels apart",
  hue: "Rotate colours around the wheel",
  saturate: "Push colour saturation",
  bw: "Black and white, high contrast",
  posterize: "Fewer colour levels",
  invert: "Negative",
  scanlines: "VHS lines and wobble",
  colorize: "Map brightness onto the current palette",
};

// Only on the master (applied to the whole picture).
export const MASTER_FX = {
  trails: "Each frame leaves an echo of the last",
  flash: "Brighten",
  strobe: "Black-out flicker on every step",
};

export const ALL_FX = { ...FX, ...MASTER_FX };

export const BLEND_MODES = ["normal", "add", "screen", "multiply", "difference"];

// Music features a modulation can follow (see analysis.js Analysis.at).
export const SOURCES = ["bass", "mid", "high", "level", "onset", "intensity", "beat"];

// Three-colour gradients (dark, mid, light) for colorize; the palette changes with the music.
export const PALETTES = [
  ["#0b0420", "#d1206b", "#ffd98a"],
  ["#020b14", "#1f7a8c", "#e8f1c4"],
  ["#100808", "#c2410c", "#fde68a"],
  ["#05050a", "#6d28d9", "#a7f3d0"],
  ["#0a0a0a", "#9ca3af", "#fafafa"],
  ["#04140a", "#16a34a", "#f0abfc"],
];

export function clamp01(x) {
  return x < 0 ? 0 : x > 1 ? 1 : x;
}

// Sum of effect amounts, clamped to 0..1.
export function add(...dicts) {
  const out = {};
  for (const d of dicts) for (const [k, v] of Object.entries(d || {})) out[k] = (out[k] || 0) + Number(v);
  for (const k in out) out[k] = clamp01(out[k]);
  return out;
}

export function hexRgb(color) {
  const c = (color || "#000000").replace("#", "");
  return [0, 2, 4].map(i => (parseInt(c.slice(i, i + 2), 16) || 0) / 255);
}
