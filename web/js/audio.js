// The song: decoding it, analysing it in a worker and playing it in time with the preview.

import { Analysis, RATE } from "./analysis.js";

let shared = null;

// The page's audio context (made on first use; phones only start it after a tap).
export function context() {
  if (!shared) shared = new (window.AudioContext || window.webkitAudioContext)();
  return shared;
}

export async function decode(blob) {
  const data = await blob.arrayBuffer();
  return await context().decodeAudioData(data);
}

// Mono samples at the analysis rate.
async function mono(buffer) {
  const length = Math.ceil(buffer.duration * RATE);
  try {
    const off = new OfflineAudioContext(1, length, RATE);
    const src = off.createBufferSource();
    src.buffer = buffer;
    src.connect(off.destination);
    src.start();
    return (await off.startRendering()).getChannelData(0);
  } catch {
    // No offline rendering at this rate: average the channels and pick the nearest samples.
    const chans = Array.from({ length: buffer.numberOfChannels }, (_, c) => buffer.getChannelData(c));
    const out = new Float32Array(length);
    const step = buffer.sampleRate / RATE;
    for (let i = 0; i < length; i++) {
      const a = Math.floor(i * step), b = Math.max(a + 1, Math.floor((i + 1) * step));
      let s = 0, n = 0;
      for (const ch of chans) for (let k = a; k < Math.min(b, ch.length); k++) { s += ch[k]; n++; }
      out[i] = n ? s / n : 0;
    }
    return out;
  }
}

export async function analyse(buffer, progress = () => {}) {
  const samples = await mono(buffer);
  return await new Promise((resolve, reject) => {
    const w = new Worker(new URL("./analysis-worker.js", import.meta.url), { type: "module" });
    w.onmessage = e => {
      if (e.data.progress !== undefined) progress(e.data.progress);
      else {
        w.terminate();
        if (e.data.error) reject(new Error(e.data.error));
        else resolve(Analysis.from(e.data.result));
      }
    };
    w.onerror = e => { w.terminate(); reject(new Error(e.message || "analysis failed")); };
    w.postMessage({ samples }, [samples.buffer]);
  });
}

// Plays the program's soundtrack (the song section, silent under title cards) from program time T.
export class Player {
  constructor(buffer) {
    this.buffer = buffer;
    this.source = null;
    this.playing = false;
    this.T0 = 0;
    this.c0 = 0;
  }

  now() {
    if (!this.playing) return this.T0;
    return this.T0 + (context().currentTime - this.c0);
  }

  start(program, T) {
    this.stop();
    const ctx = context();
    if (ctx.state === "suspended") ctx.resume();
    this.T0 = T;
    this.c0 = ctx.currentTime + 0.03;
    this.playing = true;
    if (!this.buffer) return;
    const gain = ctx.createGain();
    gain.connect(ctx.destination);
    const src = ctx.createBufferSource();
    src.buffer = this.buffer;
    src.connect(gain);
    // Where the song is at T, or when it starts if T is on an opening card.
    const songAt = program.songTime(T);
    const wait = songAt === null && T < program.pre ? program.pre - T : 0;
    if (songAt === null && T >= program.pre) return;
    const offset = songAt === null ? program.songStart : songAt;
    const left = program.songEnd - offset;
    const at = this.c0 + wait;
    // The fades, as in the export: in from the start of the section, out to its end.
    const level = o => Math.min(1, program.fadeIn > 0 ? (o - program.songStart) / program.fadeIn : 1,
      program.fadeOut > 0 ? (program.songEnd - o) / program.fadeOut : 1);
    const g = gain.gain;
    g.setValueAtTime(Math.max(0, level(offset)), at);
    const inEnd = program.songStart + program.fadeIn;
    if (program.fadeIn > 0 && offset < inEnd) g.linearRampToValueAtTime(level(inEnd), at + inEnd - offset);
    const outStart = Math.max(offset, program.songEnd - program.fadeOut);
    if (program.fadeOut > 0) {
      g.setValueAtTime(Math.max(0, level(outStart)), at + outStart - offset);
      g.linearRampToValueAtTime(0, at + left);
    }
    src.start(at, offset, left);
    this.source = src;
  }

  stop() {
    this.T0 = this.now();
    this.playing = false;
    if (this.source) {
      try { this.source.stop(); } catch { /* already stopped */ }
      this.source.disconnect();
      this.source = null;
    }
  }
}
