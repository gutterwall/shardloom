// Analyses a song off the main thread: receives {samples} (mono Float32Array at analysis.RATE).

import { analyseSamples, RATE } from "./analysis.js";

self.onmessage = e => {
  try {
    const a = analyseSamples(e.data.samples, RATE, f => self.postMessage({ progress: f }));
    self.postMessage({ result: a.toData() });
  } catch (err) {
    self.postMessage({ error: String(err && err.message || err) });
  }
};
