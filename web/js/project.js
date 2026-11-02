// The project, as plain data in the same shape as the desktop's project.json, so a project moves both ways.

import { PALETTES } from "./fx.js";
import { defaultPace } from "./pace.js";

export const CHOICES = ["random", "bag", "no_repeat", "intensity"];

export const Step = (o = {}) => ({ on: false, prob: 1, shard: null, gate: 1, speed: 1, reverse: false, jitter: 1, fx: {}, ...o });

export const Lane = (o = {}) => ({ name: "Lane", blend: "normal", opacity: 1, choice: "bag", shards: null, fx: {},
  mute: false, sources: null, ...o });

export const Pattern = (o = {}) => ({ name: "A", length: 16, steps: [], ...o });

export const Modulation = (o = {}) => ({ source: "bass", target: "master.zoom", amount: 0.3, ...o });

// A video or photo the pool is cut from; path is inside the project ("media/name").
export const Source = (o = {}) => ({ path: "", kind: "video", id: "", enabled: true, weight: 1, start: 0, end: null,
  motion: "random", shards: 2, ...o });

export const Title = (o = {}) => ({ text: "Title", subtitle: "", at: "start", duration: 4, extra: false,
  background: "none", colour: "#000000", image: "", text_colour: "#ffffff", font: "", size: 0.1,
  position: "center", fade: 0.6, shadow: true, ...o });

export const defaultCut = () => ({ count: 48, min: 0.25, max: 1.5, seed: 1, detail: 360 });

export const defaultOutput = () => ({ format: "youtube-1080", quality: "good", start: 0, end: null, fade_in: 0,
  fade_out: 1, queue: [] });

export function Project(o = {}) {
  return {
    music: "", pool: "pool", seed: 1, steps_per_beat: 4, width: 640, height: 360, fps: 30, grid: null,
    tempo_scale: 1, lanes: [], patterns: [], song: [], master_fx: {}, modulations: [],
    palettes: PALETTES.map(p => [...p]), palette_mode: "intensity", palette_bars: 8, performance: [],
    sources: [], cut: defaultCut(), titles: [], output: defaultOutput(), shard_fx: {}, pace: defaultPace(),
    ...o,
  };
}

const pickKnown = (make, d) => {
  const base = make();
  const out = {};
  for (const k of Object.keys(base)) if (d && k in d) out[k] = d[k];
  return make(out);
};

// A project from a project.json, filling in anything an older or newer version left out.
export function fromDict(d) {
  d = d || {};
  const p = pickKnown(Project, d);
  p.lanes = (d.lanes || []).map(l => pickKnown(Lane, l));
  p.patterns = (d.patterns || []).map(pt => Pattern({ ...pt, steps: (pt.steps || []).map(row => row.map(s => pickKnown(Step, s))) }));
  p.modulations = (d.modulations || []).map(m => pickKnown(Modulation, m));
  p.sources = (d.sources || []).map(s => pickKnown(Source, s));
  p.titles = (d.titles || []).map(t => pickKnown(Title, t));
  p.cut = { ...defaultCut(), ...(d.cut || {}) };
  p.output = { ...defaultOutput(), ...(d.output || {}) };
  p.pace = { ...defaultPace(), ...(d.pace || {}) };
  p.shard_fx = { ...(d.shard_fx || {}) };
  normalise(p);
  return p;
}

// Every pattern has a step row of its length for every lane.
export function normalise(p) {
  for (const pt of p.patterns) {
    while (pt.steps.length < p.lanes.length) pt.steps.push([]);
    pt.steps.length = p.lanes.length;
    for (const row of pt.steps) {
      while (row.length < pt.length) row.push(Step());
      row.length = pt.length;
    }
  }
}

// A project that does something on first play: a busy base lane, sparse overlays, beat-reactive master.
export function starterProject(music, pool, lanes = 3, seed = 1) {
  const names = ["Base", "Hits", "Texture", "Accents"];
  const blends = ["normal", "screen", "difference", "add"];
  const p = Project({ music, pool, seed });
  p.lanes = Array.from({ length: lanes }, (_, i) => Lane({ name: names[i % 4], blend: blends[i % 4],
    opacity: i === 0 ? 1 : 0.8, choice: i === 0 ? "bag" : "intensity" }));
  const a = Pattern({ name: "A" }), b = Pattern({ name: "B" });
  for (const [pat, dense] of [[a, false], [b, true]]) {
    for (let li = 0; li < lanes; li++) {
      const row = [];
      for (let s = 0; s < 16; s++) {
        if (li === 0) {
          row.push(Step({ on: s % 4 === 0 || (dense && s % 2 === 0), prob: s % 4 === 0 ? 1 : 0.5, gate: s % 4 === 0 ? 4 : 2 }));
        } else if (li === 1) {
          row.push(Step({ on: [4, 12].includes(s) || (dense && [6, 14, 15].includes(s)), prob: 0.8, gate: 1.5,
            fx: s === 12 ? { rgb_split: 0.4 } : {} }));
        } else {
          row.push(Step({ on: s % 8 === 2 || (dense && s % 4 === 2), prob: 0.6, gate: 3, speed: 0.5,
            fx: s === 10 ? { kaleido: 0.5 } : { glitch: 0.3 } }));
        }
      }
      pat.steps.push(row);
    }
  }
  p.patterns = [a, b];
  p.song = [{ pattern: 0, repeats: 4 }, { pattern: 1, repeats: 4 }];
  p.master_fx = { colorize: 0.25 };
  p.modulations = [
    Modulation({ source: "bass", target: "master.zoom", amount: 0.25 }),
    Modulation({ source: "beat", target: "master.flash", amount: 0.35 }),
    Modulation({ source: "intensity", target: "master.colorize", amount: 0.5 }),
    Modulation({ source: "high", target: "all.rgb_split", amount: 0.2 }),
    Modulation({ source: "onset", target: "master.shake", amount: 0.15 }),
  ];
  normalise(p);
  return p;
}

export function newSourceId(p) {
  const used = new Set(p.sources.map(s => s.id));
  let n = p.sources.length + 1;
  while (used.has(`s${n}`)) n++;
  return `s${n}`;
}

export const VIDEO_EXT = [".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg", ".wmv", ".mts", ".m2ts", ".3gp"];
export const PHOTO_EXT = [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".heic", ".avif"];
export const MUSIC_EXT = [".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".aif", ".aiff"];

export function kindOf(name, type = "") {
  const ext = name.slice(name.lastIndexOf(".")).toLowerCase();
  if (type.startsWith("video/") || VIDEO_EXT.includes(ext)) return "video";
  if (type.startsWith("image/") || PHOTO_EXT.includes(ext)) return "photo";
  if (type.startsWith("audio/") || MUSIC_EXT.includes(ext)) return "music";
  return null;
}
