// Shard effects: glitches, analog faults, film burns, fireworks and explosions that strike now and then,
// and Colour effects that follow the music. As events.py on the desktop; settings in project.shard_fx[name]:
//   rate 0..1 how often, sync -1..1 between / any / on the pulses, strength 0..1 how hard.

import { rand, pmod } from "./rand.js";

const E = (label, group, about, seconds, envelope, follows = "", bothWays = false, peak = 0.4) =>
  ({ label, group, about, seconds, envelope, follows, bothWays, peak });

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
};
export const NAMES = Object.keys(EVENTS);
export const GROUPS = ["Digital glitch", "Analog", "Film", "Pyrotechnics", "Colour"];

export function defaultSetting() {
  return { rate: 0, sync: 0, strength: 0.7 };
}

export function setting(project, name) {
  return { ...defaultSetting(), ...((project.shard_fx || {})[name] || {}) };
}

// 0..1 over a strike's life (age 0..1).
export function envelope(kind, age) {
  if (age < 0 || age >= 1) return 0;
  if (kind === "spike") return (1 - age) ** 2;
  if (kind === "swell") return Math.sin(Math.PI * age);
  return Math.min(1, age / 0.04, (1 - age) / 0.15);
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
  }

  reset() {
    this.steps.clear();
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
      if (s.rate > 0) {
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
      const age = (t - hit[0]) / ev.seconds;
      let amount = strength * Math.max(held, envelope(ev.envelope, age));
      if (ev.follows) {
        if (music === null) music = e.analysis.at(Math.max(0, t));
        const level = Math.min(1, Math.max(0, music[ev.follows] || 0));
        amount *= ev.bothWays ? 2 * level - 1 : level;
        hit = [hit[0], rand(p.seed, 104729, i)];     // the same direction all through the song
      }
      if (amount !== 0) out[name] = [amount, age, hit[1]];
    });
    return out;
  }
}
