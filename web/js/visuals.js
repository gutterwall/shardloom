// MilkDrop-style visuals, as visuals.py on the desktop: which preset a strike of the visuals effect draws, how it is
// mixed into the layers and over which layer, and what each step of it hears. The presets come from visuals.py
// through shaders.js (web/tools/gen_shaders.py); the renderer steps and draws them.

import { rand } from "./rand.js";
import { VISUAL_RATE, VISUAL_FEED_HEIGHT, VISUAL_PARAMS, VISUAL_PRESETS, VISUAL_BLENDS } from "./shaders.js";

export const RATE = VISUAL_RATE;
export const PRESETS = VISUAL_PRESETS;
export const BLENDS = VISUAL_BLENDS;
export const DEFAULT_OPTIONS = { presets: Object.keys(VISUAL_PRESETS), blends: Object.keys(VISUAL_BLENDS) };

// A preset's numbers in PARAMS order, for the shaders.
export function pack(key) {
  return VISUAL_PARAMS.map(name => Number(VISUAL_PRESETS[key][name]));
}

// The feedback picture's size for an output of this shape (the same for the preview and the render).
export function feedSize(width, height) {
  return [Math.max(64, Math.min(1280, Math.round(VISUAL_FEED_HEIGHT * width / Math.max(1, height)))), VISUAL_FEED_HEIGHT];
}

export function enabled(options, key, every) {
  const chosen = options[key];
  const names = (Array.isArray(chosen) ? chosen : Object.keys(every)).filter(n => n in every);
  return names.length ? names : Object.keys(every);
}

// What a strike of the visuals effect draws; hit is [amount, age 0..1, seed] from the Scheduler, t0 when the strike
// began and s its settings (events.setting). As visuals.strike().
export function strike(project, s, hit, t0, seconds) {
  const [amount, age, sd] = hit;
  const presets = enabled(s, "presets", VISUAL_PRESETS);
  const blends = enabled(s, "blends", VISUAL_BLENDS);
  const key = Math.floor(sd * 1e6);
  const preset = presets[Math.trunc(rand(project.seed, 8111, key) * presets.length)];
  const blend = blends[Math.trunc(rand(project.seed, 8117, key) * blends.length)];
  const lanes = project.lanes.map((lane, li) => [lane, li]).filter(([lane]) => !lane.mute).map(([, li]) => li);
  const after = blend === "behind" || !lanes.length ? project.lanes.length - 1
    : lanes[Math.trunc(rand(project.seed, 8123, key) * lanes.length)];
  return { preset, blend, after, amount, seed: sd, t0,
    steps: Math.floor(age * seconds * RATE + 1e-6) };
}

// What a visual step at song time t hears: bass, mid, high, onset and the beat (1 on it, falling away).
export function musicAt(analysis, t) {
  const m = analysis.at(Math.max(0, t));
  return ["bass", "mid", "high", "onset", "beat"].map(k => Number(m[k] || 0));
}
