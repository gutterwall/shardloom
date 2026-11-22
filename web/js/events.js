// Shard effects: glitches, analog faults, film burns, fireworks and explosions that strike now and then,
// Colour effects that follow the music, and One layer effects (lens, mirror) that land on layers instead of the
// whole picture. As events.py on the desktop; settings in project.shard_fx[name]:
//   rate 0..1 how often, sync -1..1 between / any / on the pulses, strength 0..1 how hard, and an effect's options.

import { rand, pmod, pyround } from "./rand.js";
import { DEFAULT_OPTIONS as DEFAULT_VISUALS } from "./visuals.js";

const E = (label, group, about, seconds, envelope, follows = "", bothWays = false, peak = 0.4, layer = false,
  options = {}, stack = false) => ({ label, group, about, seconds, envelope, follows, bothWays, peak, layer, options,
  stack });

export const EVENTS = {
  datamosh: E("Datamosh", "Digital glitch", "Blocks smear the last frame along with the motion", 0.6, "flat"),
  pixel_sort: E("Pixel sort", "Digital glitch", "Bright pixels drag into streaks", 0.5, "swell"),
  corrupt: E("Corruption", "Digital glitch", "Broken blocks: swapped channels, wrong colours, copies", 0.3, "flat"),
  tear: E("Slice tear", "Digital glitch", "Horizontal slices torn sideways", 0.25, "spike"),
  vhs: E("VHS tracking", "Analog", "A rolling tracking band with snow and wobble", 0.8, "swell"),
  crt: E("CRT", "Analog", "Curved glass, scanlines, phosphor mask and glow", 1.0, "swell"),
  roll: E("Vertical roll", "Analog", "The picture loses hold and rolls", 0.5, "spike"),
  bleed: E("Chroma bleed", "Analog", "Colour smears off the picture with a ghost", 0.7, "swell"),
  burn: E("Film burn", "Film", "The frame catches in the gate and burns through", 1.6, "flat"),
  leak: E("Light leak", "Film", "Warm light floods in from the edge", 1.4, "swell"),
  damage: E("Film damage", "Film", "Scratches, dust, weave and flicker", 1.2, "flat"),
  fireworks: E("Fireworks", "Pyrotechnics", "Bursts of sparks that fall and fade", 2.2, "flat"),
  explosion: E("Explosion", "Pyrotechnics", "A fireball, shockwave, flash and shake", 1.4, "flat"),
  hue_shift: E("Hue shift", "Colour", "Turns the colours round the wheel, further when the music is intense",
    0.4, "flat", "intensity", false, 1.0),
  brightness: E("Brightness", "Colour", "Brighter when the music is loud, darker when it is quiet",
    0.4, "flat", "level", true, 1.0),
  saturation: E("Saturation", "Colour", "Richer colour when the music is intense, greyer when it is calm",
    0.4, "flat", "intensity", true, 1.0),
  lens: E("Colour lens", "One layer", "A coloured lens fades in over one of the overlay layers: " +
    "a new random colour each time", 3.0, "fade", "", false, 0.4, true),
  mirror: E("Mirror", "One layer", "Folds layers into mirror images or a kaleidoscope that zooms " +
    "from the middle or the edges, on the beat", 4.0, "flat", "", false, 0.4, true,
    { style: "kaleido", segments: 6, zoom: "middle", depth: 0.5, layers: 1, beat: true, leaves: 0 }),
  // Last, so the effects before it keep their strikes (the index is part of each draw).
  flip: E("Colour flip", "Colour", "A strobe that turns over one or more of the red, green and blue " +
    "channels, a new mix on every flash", 1.5, "flat", "", false, 0.4, false, { flashes: 2 }),
  // After flip for the same reason.
  visuals: E("MilkDrop", "Visuals", "A MilkDrop-style music visual, drawn live and mixed into the layers: " +
    "plus, minus, occlude, overlay, multiply or behind them as a background", 8.0, "fade", "", false, 0.25, false,
    DEFAULT_VISUALS, true),
};
export const NAMES = Object.keys(EVENTS);
// The whole-picture ones, in shaders.EVENTS order.
export const PICTURE = NAMES.filter(n => !EVENTS[n].layer && !EVENTS[n].stack);
export const LAYER = NAMES.filter(n => EVENTS[n].layer);      // the One layer ones
export const GROUPS = ["Digital glitch", "Analog", "Film", "Pyrotechnics", "Colour", "One layer", "Visuals"];

export const MIRROR_STYLES = ["mirror", "quad", "kaleido"];   // shaders.EFFECTS fold_style 0, 1, 2
export const MIRROR_ZOOMS = ["middle", "edges", "none"];
export const FLASHES = [1, 2, 4];                              // Colour flip flashes per beat

export function defaultSetting() {
  return { rate: 0, sync: 0, strength: 0.7 };
}

export function setting(project, name) {
  const ev = EVENTS[name];
  const out = { ...defaultSetting(), ...(ev ? ev.options : {}), ...((project.shard_fx || {})[name] || {}) };
  // Lists (the visuals' presets and blends) are copied, so changing them never changes the defaults or the project.
  for (const k of Object.keys(out)) if (Array.isArray(out[k])) out[k] = [...out[k]];
  return out;
}

// 0..1 over a strike's life (age 0..1).
export function envelope(kind, age) {
  if (age < 0 || age >= 1) return 0;
  if (kind === "spike") return (1 - age) ** 2;
  if (kind === "swell") return Math.sin(Math.PI * age);
  if (kind === "fade") return Math.min(1, age / 0.4, (1 - age) / 0.25);
  return Math.min(1, age / 0.04, (1 - age) / 0.15);
}

// As hsv() in the shaders (h 0..1).
export function hsv(h, s, v) {
  const k = n => Math.min(1, Math.max(0, Math.abs(pmod(h * 6 + n, 6) - 3) - 1));
  return [0, 4, 2].map(n => v * (1 + (k(n) - 1) * s));
}

// Which layers a strike of a One layer effect lands on: count of them, from its seed.
// The lens goes on the overlay layers (above the first) when there are any; the mirror on any layer.
export function pickLayers(project, name, seed, count) {
  let lanes = project.lanes.map((lane, li) => [lane, li]).filter(([lane]) => !lane.mute).map(([, li]) => li);
  if (name === "lens" && lanes.length > 1) {
    const over = lanes.filter(li => li > 0);
    if (over.length) lanes = over;
  }
  const key = Math.floor(seed * 1e6);
  const i = NAMES.indexOf(name);
  const order = new Map(lanes.map(li => [li, rand(project.seed, 3571, i, li, key)]));
  lanes.sort((a, b) => order.get(a) - order.get(b) || a - b);
  return lanes.slice(0, Math.max(1, count));
}

// Which of red, green and blue a Colour flip flash with this seed turns over (as shaders.EVENTS).
export function flipChannels(sd) {
  const m = 1 + Math.min(6, Math.trunc(sd * 7));
  return [!!(m & 1), !!(m & 2), !!(m & 4)];
}

// Colour flip at song position beats: [lit now, the seed of this flash], for a strike with seed sd.
export function strobe(project, beats, sd) {
  let f = Math.trunc(Number(setting(project, "flip").flashes));
  if (!FLASHES.includes(f)) f = 2;
  const phase = beats * f;
  const k = Math.floor(phase);
  return [phase - k < 0.5, rand(project.seed, 6007, k, Math.floor(sd * 1e6))];
}

// layer index -> the shader settings of the One layer effects striking it (see shaders.EFFECTS).
// hits: Scheduler.at(t); beats: the song position in beats, for the mirror's beat moves.
export function layerStrikes(project, hits, beats) {
  const out = {};
  const put = (li, values) => { out[li] = { ...(out[li] || {}), ...values }; };
  let hit = hits.lens;
  if (hit) {
    const [amount, , sd] = hit;
    const [r, g, b] = hsv(pmod(sd * 7, 1), 0.85, 1);
    for (const li of pickLayers(project, "lens", sd, 1)) put(li, { lens: amount, lens_r: r, lens_g: g, lens_b: b });
  }
  hit = hits.mirror;
  if (hit) {
    const [amount, age, sd] = hit;
    const s = setting(project, "mirror");
    const style = MIRROR_STYLES.includes(s.style) ? MIRROR_STYLES.indexOf(s.style) : 2;
    const segments = Math.min(12, Math.max(3, pyround(Number(s.segments))));
    const depth = Math.min(1, Math.max(0, Number(s.depth)));
    const frac = beats - Math.floor(beats);
    let zoom, turn;
    if (s.beat) {
      zoom = depth * (1 - frac) ** 2;                     // punches in on each beat and eases off
      const notch = [Math.PI / 2, Math.PI / 4, Math.PI / segments][style];
      turn = Math.floor(beats) * notch;                   // a notch round on each beat
    } else {
      zoom = depth * Math.sin(Math.PI * Math.min(1, Math.max(0, age)));
      turn = age * 1.2;
    }
    zoom *= { middle: 1, edges: -1 }[s.zoom] ?? 0;
    turn += sd * 2 * Math.PI;
    const leaves = Math.min(1, Math.max(0, Number(s.leaves ?? 0)));
    // The leaves' colours: new ones on each beat when on the beat.
    const leafSeed = s.beat ? pmod(sd + 0.6180339887 * Math.floor(beats), 1) : sd;
    for (const li of pickLayers(project, "mirror", sd, Math.trunc(Number(s.layers)))) {
      put(li, { fold: amount, fold_style: style, fold_segments: segments, fold_zoom: zoom,
        fold_turn: pmod(turn, 2 * Math.PI), fold_leaves: leaves, fold_seed: leafSeed });
    }
  }
  return out;
}

// The chance of a strike on a step with this pulse (0 between the beats .. 1 on a strong beat).
export function chance(rate, sync, pulse, peak = 0.4) {
  const weight = 1 + sync * (2 * pulse - 1);
  return Math.max(0, Math.min(1, peak * rate ** 1.5 * weight));
}

// Which shard effects are striking at a time, for one engine.
export class Scheduler {
  constructor(engine) {
    this.engine = engine;
    this.steps = new Map();
    this.manual = {};          // name -> time it was set off by hand (not saved)
    // Effects drawn into the stack take turns (turns()): name -> [settings key, last step looked at, start steps].
    this.turnsBy = {};
    this.started = {};         // name -> when the strike at the last at() began (exactly)
  }

  reset() {
    this.steps.clear();
    this.turnsBy = {};
  }

  // The steps up to now on which an effect that takes turns (the visuals) starts a strike: as any effect, but a new
  // one only once the last has finished. Worked out from the first step on and kept, as events.Scheduler.turns().
  turns(i, name, s, now) {
    const p = this.engine.project;
    const ev = EVENTS[name];
    const key = JSON.stringify([p.seed, Number(s.rate), Number(s.sync)]);
    let got = this.turnsBy[name];
    if (!got || got[0] !== key) got = [key, -1, []];
    const [, done, starts] = got;
    for (let g = done + 1; g <= now; g++) {
      const [t0, pulse] = this.step(g);
      if (starts.length && t0 < this.step(starts[starts.length - 1])[0] + ev.seconds) continue;
      if (rand(p.seed, 7919, i, g) < chance(Number(s.rate), Number(s.sync), pulse, ev.peak)) starts.push(g);
    }
    this.turnsBy[name] = [key, Math.max(done, now), starts];
    return starts;
  }

  // [start time, pulse] of step g; the pulse is the beat itself and how hard the music hits there.
  step(g) {
    let got = this.steps.get(g);
    if (got === undefined) {
      const e = this.engine;
      const t = e.timeOfStep(g);
      const onBeat = pmod(g, Math.max(1, e.project.steps_per_beat)) === 0 ? 1 : 0;
      const onset = e.analysis.at(Math.max(0, t)).onset || 0;
      got = [t, 0.6 * onBeat + 0.4 * Math.min(1, Math.max(0, onset))];
      if (this.steps.size > 4096) this.steps.clear();
      this.steps.set(g, got);
    }
    return got;
  }

  fire(name, t) {
    this.manual[name] = t;
  }

  // name -> [amount, age 0..1, seed 0..1] for each effect striking at t; Colour amounts may run -1..1.
  at(t) {
    const e = this.engine, p = e.project;
    const out = {};
    let music = null;
    const now = e.stepAt(t);
    NAMES.forEach((name, i) => {
      const ev = EVENTS[name];
      const s = setting(p, name);
      const strength = Number(s.strength);
      if (strength <= 0) return;
      let hit = null;
      let held = 0;            // Colour effects: the strongest of the strikes still going, so they join up
      if (s.rate > 0 && ev.stack) {
        const starts = this.turns(i, name, s, now);
        let k = starts.length - 1;
        while (k >= 0 && starts[k] > now) k--;
        if (k >= 0) {
          const t0 = this.step(starts[k])[0];
          if (t0 <= t && t < t0 + ev.seconds) hit = [t0, rand(p.seed, 104729, i, starts[k])];
        }
      } else if (s.rate > 0) {
        for (let g = now; g >= 0; g--) {
          const [t0, pulse] = this.step(g);
          if (t0 < t - ev.seconds) break;
          if (t0 <= t && rand(p.seed, 7919, i, g) < chance(Number(s.rate), Number(s.sync), pulse, ev.peak)) {
            if (hit === null) hit = [t0, rand(p.seed, 104729, i, g)];
            if (!ev.follows) break;
            held = Math.max(held, envelope(ev.envelope, (t - t0) / ev.seconds));
          }
        }
      }
      const byHand = this.manual[name];
      if (byHand !== undefined && byHand <= t && t < byHand + ev.seconds && (hit === null || byHand > hit[0])) {
        hit = [byHand, (byHand * 7.31) % 1];
      }
      if (hit === null) return;
      this.started[name] = hit[0];
      const age = (t - hit[0]) / ev.seconds;
      let amount = strength * Math.max(held, envelope(ev.envelope, age));
      if (ev.follows) {
        if (music === null) music = e.analysis.at(Math.max(0, t));
        const level = Math.min(1, Math.max(0, music[ev.follows] || 0));
        amount *= ev.bothWays ? 2 * level - 1 : level;
        hit = [hit[0], rand(p.seed, 104729, i)];     // the same direction all through the song
      }
      let sd = hit[1];
      if (name === "flip") {
        let lit;
        [lit, sd] = strobe(p, e.beats(t), sd);
        if (!lit) return;
      }
      if (amount !== 0) out[name] = [amount, age, sd];
    });
    return out;
  }
}
