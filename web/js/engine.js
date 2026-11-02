// The sequencer engine, a port of sequencer.Engine: time runs on the music's beats, and every random
// decision comes from the seed and the step number, so the preview and an export show the same picture.

import * as fxlib from "./fx.js";
import * as pacelib from "./pace.js";
import { Step } from "./project.js";
import { rand, pmod, pyround } from "./rand.js";
import { Scheduler } from "./events.js";

export class Engine {
  // shards: [{id, frames, energy, source_id}], analysis: an Analysis.
  constructor(project, shards, analysis, poolFps = 30) {
    this.project = project;
    this.shards = new Map(shards.map(s => [s.id, s]));
    this.lengths = new Map(shards.map(s => [s.id, s.frames]));
    this.poolFps = poolFps;
    this.baseAnalysis = analysis;
    this.livePattern = 0;
    this.queuedPattern = null;
    // Play the song arrangement (if there is one) rather than the live pattern.
    this.songMode = project.song.length > 0;
    this.fill = false;
    this.punch = {};
    this.events = new Scheduler(this);
    this.retime();
  }

  // ---- clock ---------------------------------------------------------------

  // Rebuilds the clock and the palette schedule after the grid or tempo changed.
  retime() {
    let a = this.baseAnalysis;
    const g = this.project.grid;
    if (g) a = a.withGrid(Number(g.bpm), Number(g.offset || 0));
    this.analysis = a;
    this._palettes = [];
    this.events.reset();
    this.reset();
  }

  reset() {
    this._done = -1;
    this._voices = this.project.lanes.map(() => []);
    this._bags = new Map();
    this._bagRound = new Map();
    this._last = new Map();
    this._patternAt = new Map();
    // Shard timing: per lane, the phrases so far as [first step, steps per play, plays, shard, beats].
    this._phrases = new Map();
    this._paceKey = null;
    this._livePatternRun = this.livePattern;
  }

  beats(t) {
    return this.analysis.beatPosition(t) * this.project.tempo_scale;
  }

  timeOfStep(g) {
    return this.analysis.timeAtBeat(g / this.project.steps_per_beat / this.project.tempo_scale);
  }

  stepAt(t) {
    return Math.floor(this.beats(t) * this.project.steps_per_beat + 1e-9);
  }

  // ---- arrangement -----------------------------------------------------------

  // [pattern index, step within the pattern] for global step g.
  patternForStep(g) {
    const p = this.project;
    if (!p.patterns.length) return [0, 0];
    if (p.song.length && !this._live()) {
      const span = e => p.patterns[e.pattern].length * Math.max(1, Math.trunc(e.repeats || 1));
      const total = p.song.reduce((a, e) => a + span(e), 0);
      let pos = total ? pmod(g, total) : 0;
      for (const e of p.song) {
        if (pos < span(e)) return [e.pattern, pmod(pos, p.patterns[e.pattern].length)];
        pos -= span(e);
      }
    }
    let idx = this._patternAt.get(g);
    if (idx === undefined) {
      idx = p.performance.length ? this._recordedPattern(g) : null;
      if (idx === null) idx = this._livePatternRun;
    }
    idx = Math.min(idx, p.patterns.length - 1);
    return [idx, pmod(g, p.patterns[idx].length)];
  }

  // The pattern a recorded performance (made on the desktop) switched to by step g, or null.
  _recordedPattern(g) {
    let value = null;
    for (const ev of this.project.performance) {
      if (ev.type === "pattern" && (ev.step || 0) <= g) value = Math.trunc(ev.value);
    }
    return value;
  }

  // Whether the live (or recorded) pattern plays instead of the song.
  _live() {
    return !this.songMode || this.project.performance.some(e => e.type === "pattern");
  }

  // Punch-ins: recorded ones when the project has a performance, else the live ones.
  punchAt(t) {
    if (!this.project.performance.some(e => e.type === "punch")) return { ...this.punch };
    const held = {};
    for (const ev of this.project.performance) {
      if (ev.type === "punch" && (ev.t || 0) <= t) {
        if (ev.on) held[ev.fx] = Number(ev.amount ?? 1);
        else delete held[ev.fx];
      }
    }
    return held;
  }

  // ---- triggering ------------------------------------------------------------

  _candidates(lane) {
    let ids = (lane.shards !== null && lane.shards !== undefined ? lane.shards : [...this.shards.keys()])
      .filter(i => this.shards.has(i));
    if (lane.sources !== null && lane.sources !== undefined) {
      ids = ids.filter(i => lane.sources.includes(this.shards.get(i).source_id || ""));
    }
    return ids.length ? ids : [...this.shards.keys()];
  }

  _choose(li, lane, step, g, t) {
    const seed = this.project.seed;
    if (step.shard !== null && step.shard !== undefined && this.shards.has(step.shard)) return step.shard;
    const ids = this._candidates(lane);
    if (ids.length === 1) return ids[0];
    const r = rand(seed, g, li, 7);
    if (lane.choice === "bag") {
      let bag = this._bags.get(li);
      if (!bag || !bag.length) {
        const rnd = this._bagRound.get(li) || 0;
        this._bagRound.set(li, rnd + 1);
        const keys = new Map(ids.map(i => [i, rand(seed, li, rnd, i, 11)]));
        bag = [...ids].sort((a, b) => keys.get(a) - keys.get(b));
        // A new round never starts with the shard that ended the last one.
        if (bag.length > 1 && bag[bag.length - 1] === this._last.get(li)) {
          [bag[0], bag[bag.length - 1]] = [bag[bag.length - 1], bag[0]];
        }
        this._bags.set(li, bag);
      }
      return bag.pop();
    }
    if (lane.choice === "no_repeat") {
      const last = this._last.get(li);
      let pool = ids.filter(i => i !== last);
      if (!pool.length) pool = ids;
      return pool[Math.trunc(r * pool.length)];
    }
    if (lane.choice === "intensity") {
      const target = this.analysis.at(t).intensity ?? 0.5;
      const weights = ids.map(i => Math.exp(-((((this.shards.get(i).energy ?? 0.5) - target) / 0.2) ** 2)) + 1e-6);
      let pick = r * weights.reduce((a, b) => a + b, 0);
      for (let k = 0; k < ids.length; k++) {
        pick -= weights[k];
        if (pick <= 0) return ids[k];
      }
      return ids[ids.length - 1];
    }
    return ids[Math.trunc(r * ids.length)];
  }

  _trigger(g) {
    const p = this.project;
    if (this.queuedPattern !== null) {
      const [, pos] = this.patternForStep(g);
      if (pos === 0) {
        this._livePatternRun = this.livePattern = this.queuedPattern;
        this.queuedPattern = null;
      }
    }
    const [pi, si] = this.patternForStep(g);
    this._patternAt.set(g, pi);
    const pace = pacelib.settings(p);
    if (pace.on) {
      this._paced(g, pace);
      return;
    }
    if (!p.patterns.length) return;
    const pattern = p.patterns[pi];
    const t = this.timeOfStep(g);
    p.lanes.forEach((lane, li) => {
      if (li >= pattern.steps.length) return;
      const step = pattern.steps[li][si];
      if (!step || !step.on) return;
      const prob = this.fill ? 1 : step.prob;
      if (rand(p.seed, g, li, 3) >= prob) return;
      const shard = this._choose(li, lane, step, g, t);
      this._last.set(li, shard);
      const length = this.lengths.get(shard) || 1;
      let start = pmod(Math.trunc(rand(p.seed, g, li, 5) * step.jitter * length), Math.max(1, length));
      if (step.reverse && step.jitter === 0) start = length - 1;
      const voices = this._voices[li];
      voices.push({ lane: li, step: g, shard, t0: t, t1: this.timeOfStep(g + Math.max(0.05, step.gate)),
        speed: step.speed, reverse: step.reverse, start, fx: { ...step.fx } });
      // Only the newest few can still be sounding.
      if (voices.length > 4) voices.splice(0, voices.length - 4);
    });
  }

  // ---- shard timing --------------------------------------------------------------

  // The phrase lane li is playing at step g: [first step, steps per play, plays, shard, beats].
  phrase(li, g, pace = null) {
    const p = this.project;
    pace = pace || pacelib.settings(p);
    if (li >= p.lanes.length || !this.shards.size || g < 0) return null;
    const key = JSON.stringify([pace, p.steps_per_beat, p.seed]);
    if (key !== this._paceKey) {
      // Changed while playing: carry on from here with the new timing.
      this._phrases = new Map();
      this._paceKey = key;
    }
    if (!this._phrases.has(li)) this._phrases.set(li, []);
    const phrases = this._phrases.get(li);
    if (phrases.length && g < phrases[0][0]) return null;
    const endOf = ph => ph[0] + ph[1] * ph[2];
    // Phrases count from the start of the song, so a render from any point lands on the same ones.
    let start = phrases.length ? endOf(phrases[phrases.length - 1]) : 0;
    while (!phrases.length || g >= endOf(phrases[phrases.length - 1])) {
      const t = this.timeOfStep(start);
      const intensity = this.analysis.at(Math.max(0, t)).intensity ?? 0.5;
      const beats = pacelib.pick(pace, intensity, rand(p.seed, start, li, 61));
      const every = Math.max(1, pyround(beats * p.steps_per_beat));
      const plays = Math.max(1, Math.trunc(pace.repeats));
      const shard = this._choose(li, p.lanes[li], Step(), start, t);
      this._last.set(li, shard);
      phrases.push([start, every, plays, shard, beats]);
      if (phrases.length > 64) phrases.splice(0, phrases.length - 64);
      start += every * plays;
    }
    for (let k = phrases.length - 1; k >= 0; k--) if (phrases[k][0] <= g) return phrases[k];
    return phrases[0];
  }

  _paced(g, pace) {
    const p = this.project;
    const t = this.timeOfStep(g);
    p.lanes.forEach((lane, li) => {
      const ph = this.phrase(li, g, pace);
      if (!ph) return;
      const [first, every, , shard] = ph;
      if ((g - first) % every) return;
      const length = this.lengths.get(shard) || 1;
      // Every play of a phrase starts on the same frame, so it repeats.
      const start = pmod(Math.trunc(rand(p.seed, first, li, 5) * length), Math.max(1, length));
      const voices = this._voices[li];
      voices.push({ lane: li, step: g, shard, t0: t, t1: this.timeOfStep(g + every), speed: 1, reverse: false, start, fx: {} });
      if (voices.length > 4) voices.splice(0, voices.length - 4);
    });
  }

  advance(t) {
    const g = this.stepAt(t);
    // Went back in time: replay from the start so choices stay identical.
    if (g < this._done) this.reset();
    const start = this._done < 0 ? Math.min(g, this.stepAt(0)) : this._done + 1;
    for (let s = Math.max(this._done + 1, start); s <= g; s++) this._trigger(s);
    this._done = Math.max(this._done, g);
    return g;
  }

  // ---- the picture at time t ---------------------------------------------------

  paletteIndex(bar) {
    const p = this.project;
    const count = Math.max(1, p.palettes.length);
    if (p.palette_mode === "bars") return pmod(Math.floor(bar / Math.max(1, p.palette_bars)), count);
    if (p.palette_mode !== "intensity") return 0;
    // Advance one palette each time the section's intensity level (low / mid / high) changes.
    const seq = this._palettes;
    while (seq.length <= bar) {
      const b = seq.length;
      const tt = this.timeOfStep(b * 4 * p.steps_per_beat);
      const v = this.analysis.at(Math.max(0, tt)).intensity || 0;
      const level = v < 0.33 ? 0 : v < 0.66 ? 1 : 2;
      const idx = b === 0 ? 0 : (seq[b - 1][0] + (level !== seq[b - 1][1] ? 1 : 0)) % count;
      seq.push([idx, level]);
    }
    return seq[Math.max(0, bar)][0];
  }

  state(t) {
    const g = this.advance(t);
    const p = this.project;
    const music = this.analysis.at(t);
    const mods = {};
    for (const m of p.modulations) {
      const dot = m.target.indexOf(".");
      const scope = m.target.slice(0, dot), name = m.target.slice(dot + 1);
      mods[scope] = mods[scope] || {};
      mods[scope][name] = (mods[scope][name] || 0) + m.amount * (music[m.source] || 0);
    }
    const lanes = p.lanes.map((lane, li) => {
      let voice = null;
      const vs = this._voices[li] || [];
      for (let k = vs.length - 1; k >= 0; k--) {
        if (vs[k].t0 <= t && t < vs[k].t1) { voice = vs[k]; break; }
      }
      let frame = 0;
      if (voice) {
        const length = this.lengths.get(voice.shard) || 1;
        const n = Math.trunc((t - voice.t0) * this.poolFps * voice.speed);
        frame = pmod(voice.reverse ? voice.start - n : voice.start + n, Math.max(1, length));
      }
      const fx = fxlib.add(lane.fx, voice ? voice.fx : {}, mods.all, mods[`lane${li}`]);
      return { voice: lane.mute ? null : voice, frame, fx, opacity: lane.opacity, blend: lane.blend };
    });
    const master = fxlib.add(p.master_fx, mods.master, this.punchAt(t));
    const bar = Math.floor(Math.max(0, this.beats(t)) / 4);
    const palette = p.palettes.length ? p.palettes[this.paletteIndex(bar) % p.palettes.length] : fxlib.PALETTES[0];
    const [pi] = this.patternForStep(g);
    return { t, step: g, pattern: pi, lanes, master, palette, music, events: this.events.at(t) };
  }
}
