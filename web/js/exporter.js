// Renders a project to an MP4 on the phone. With WebCodecs it renders frame by frame, exactly, as the desktop
// export does; without it (older phones) it records the program playing in real time.

import { Muxer, ArrayBufferTarget } from "../vendor/mp4-muxer.mjs";
import { Engine } from "./engine.js";
import { Renderer } from "./renderer.js";
import { context } from "./audio.js";
import * as formats from "./formats.js";

// H.264 levels: [name hex, max macroblocks a frame, max macroblocks a second].
const LEVELS = [["1e", 1620, 40500], ["1f", 3600, 108000], ["20", 5120, 216000], ["28", 8192, 245760],
  ["2a", 8704, 522240], ["32", 22080, 589824], ["33", 36864, 983040], ["34", 36864, 2073600]];

function codecsFor(width, height, fps) {
  const mbs = Math.ceil(width / 16) * Math.ceil(height / 16);
  const levels = LEVELS.filter(([, fs, rate]) => mbs <= fs && mbs * fps <= rate).map(([l]) => l);
  return ["6400", "4d00", "4200"].flatMap(profile => levels.slice(0, 3).map(l => `avc1.${profile}${l}`));
}

async function videoConfig(width, height, fps, bitrate) {
  if (typeof VideoEncoder === "undefined") return null;
  for (const codec of codecsFor(width, height, fps)) {
    const config = { codec, width, height, bitrate, framerate: fps, avc: { format: "avc" } };
    try {
      if ((await VideoEncoder.isConfigSupported(config)).supported) return config;
    } catch { /* try the next */ }
  }
  return null;
}

// AAC, or Opus where the browser has no AAC encoder (Chrome on Linux); both go in the MP4.
async function audioConfig(sampleRate) {
  if (typeof AudioEncoder === "undefined") return null;
  for (const [codec, muxed] of [["mp4a.40.2", "aac"], ["opus", "opus"]]) {
    const config = { codec, sampleRate, numberOfChannels: 2, bitrate: 192000 };
    try {
      if ((await AudioEncoder.isConfigSupported(config)).supported) return { ...config, muxed };
    } catch { /* try the next */ }
  }
  return null;
}

const tick = () => new Promise(r => setTimeout(r, 0));

// What this browser can do: "exact" (WebCodecs), "realtime" (MediaRecorder) or null.
export async function exportMode(width, height, fps) {
  const v = await videoConfig(width, height, fps, formats.bitrate(width, height, fps, "good"));
  const a = await audioConfig(context().sampleRate);
  if (v && a) return "exact";
  if (typeof MediaRecorder !== "undefined" && HTMLCanvasElement.prototype.captureStream) return "realtime";
  return null;
}

// Renders the project; job: {project, shards, analysis, poolFps, frames, buffer, program, width, height,
// titleImages, progress(done, total), cancelled()}. Returns {blob, ext}.
export async function render(job) {
  const { width, height } = job;
  const fps = job.project.fps;
  const bitrate = formats.bitrate(width, height, fps, job.project.output.quality);
  const vconf = await videoConfig(width, height, fps, bitrate);
  const aconf = job.buffer ? await audioConfig(job.buffer.sampleRate) : null;
  const canvas = document.createElement("canvas");
  const renderer = new Renderer(canvas, width, height, job.frames);
  renderer.titleImages = job.titleImages || {};
  const engine = new Engine(JSON.parse(JSON.stringify(job.project)), job.shards, job.analysis, job.poolFps);
  try {
    if (vconf && (aconf || !job.buffer)) return await exact(job, renderer, engine, vconf, aconf, fps);
    return await realtime(job, renderer, engine, fps, bitrate);
  } finally {
    renderer.dispose();
  }
}

function channels(buffer, program) {
  const chans = [buffer.getChannelData(0), buffer.getChannelData(Math.min(1, buffer.numberOfChannels - 1))];
  return program.audio(chans, buffer.sampleRate);
}

async function exact(job, renderer, engine, vconf, aconf, fps) {
  const { program } = job;
  const frames = Math.round(program.duration * fps);
  const muxer = new Muxer({
    target: new ArrayBufferTarget(),
    video: { codec: "avc", width: vconf.width, height: vconf.height, frameRate: fps },
    audio: aconf ? { codec: aconf.muxed, numberOfChannels: 2, sampleRate: aconf.sampleRate } : undefined,
    fastStart: "in-memory",
    firstTimestampBehavior: "offset",
  });
  let failure = null;
  const venc = new VideoEncoder({ output: (c, m) => muxer.addVideoChunk(c, m), error: e => { failure = e; } });
  venc.configure({ ...vconf, latencyMode: "quality" });
  let aenc = null, audio = null, apos = 0;
  const rate = aconf ? aconf.sampleRate : 0;
  const CHUNK = 1024;
  if (aconf) {
    aenc = new AudioEncoder({ output: (c, m) => muxer.addAudioChunk(c, m), error: e => { failure = e; } });
    const { muxed, ...config } = aconf;
    aenc.configure(config);
    audio = channels(job.buffer, program);
  }
  const want = Math.round((frames / fps) * rate);
  const pushAudio = upTo => {
    while (apos < Math.min(upTo, want)) {
      const n = Math.min(CHUNK, want - apos);
      const data = new Float32Array(n * 2);
      for (let c = 0; c < 2; c++) {
        const src = audio[c];
        for (let k = 0; k < n; k++) data[c * n + k] = apos + k < src.length ? src[apos + k] : 0;
      }
      const ad = new AudioData({ format: "f32-planar", sampleRate: rate, numberOfFrames: n, numberOfChannels: 2,
        timestamp: Math.round((apos / rate) * 1e6), data });
      aenc.encode(ad);
      ad.close();
      apos += n;
    }
  };
  engine.reset();
  for (let i = 0; i < frames; i++) {
    if (failure) throw failure;
    if (job.cancelled()) throw new Error("cancelled");
    const T = i / fps;
    await job.frames.ensure(renderer.needs(engine, program, T));
    renderer.frame(engine, program, T);
    const vf = new VideoFrame(renderer.canvas, { timestamp: Math.round((i * 1e6) / fps), duration: Math.round(1e6 / fps) });
    venc.encode(vf, { keyFrame: i % Math.round(fps * 2) === 0 });
    vf.close();
    // Keep the audio level with the video so the file interleaves well.
    if (aenc) pushAudio(Math.floor(((i + 1) / fps) * rate));
    while (venc.encodeQueueSize > 4) await new Promise(r => setTimeout(r, 2));
    job.progress(i + 1, frames);
    if (i % 2 === 0) await tick();
  }
  if (aenc) pushAudio(want);
  await venc.flush();
  if (aenc) await aenc.flush();
  if (failure) throw failure;
  muxer.finalize();
  return { blob: new Blob([muxer.target.buffer], { type: "video/mp4" }), ext: "mp4" };
}

const RECORDER_TYPES = ["video/mp4;codecs=avc1.640028,mp4a.40.2", "video/mp4", "video/webm;codecs=vp9,opus",
  "video/webm;codecs=vp8,opus", "video/webm"];

// Plays the program in real time onto the canvas and records it.
async function realtime(job, renderer, engine, fps, bitrate) {
  const { program } = job;
  const type = RECORDER_TYPES.find(t => MediaRecorder.isTypeSupported(t)) || "";
  const ctx = context();
  if (ctx.state === "suspended") await ctx.resume();
  const stream = renderer.canvas.captureStream(fps);
  let source = null;
  if (job.buffer) {
    const audio = channels(job.buffer, program);
    const buf = ctx.createBuffer(2, audio[0].length, job.buffer.sampleRate);
    buf.copyToChannel(audio[0], 0);
    buf.copyToChannel(audio[1], 1);
    const dest = ctx.createMediaStreamDestination();
    source = ctx.createBufferSource();
    source.buffer = buf;
    source.connect(dest);
    for (const t of dest.stream.getAudioTracks()) stream.addTrack(t);
  }
  const rec = new MediaRecorder(stream, { mimeType: type || undefined, videoBitsPerSecond: bitrate, audioBitsPerSecond: 192000 });
  const parts = [];
  rec.ondataavailable = e => { if (e.data.size) parts.push(e.data); };
  const stopped = new Promise(r => { rec.onstop = r; });
  engine.reset();
  // Warm up the first frames before the clock starts.
  await job.frames.ensure(renderer.needs(engine, program, 0));
  renderer.frame(engine, program, 0);
  rec.start(1000);
  const c0 = ctx.currentTime + 0.05;
  if (source) source.start(c0);
  await new Promise((resolve, reject) => {
    const step = () => {
      try {
        if (job.cancelled()) throw new Error("cancelled");
        const T = Math.max(0, ctx.currentTime - c0);
        if (T >= program.duration) return resolve();
        const st = program.songTime(T);
        if (st !== null) {
          const state = engine.state(st);
          job.frames.prefetch(state.lanes.filter(l => l.voice).map(l => [l.voice.shard, l.frame, l.voice.reverse ? -1 : 1]));
        }
        renderer.frame(engine, program, T);
        job.progress(Math.round(T * fps), Math.round(program.duration * fps));
        requestAnimationFrame(step);
      } catch (e) {
        reject(e);
      }
    };
    requestAnimationFrame(step);
  }).finally(() => {
    if (source) try { source.stop(); } catch { /* done */ }
    rec.stop();
  });
  await stopped;
  const mime = rec.mimeType || type || "video/webm";
  return { blob: new Blob(parts, { type: mime }), ext: mime.includes("mp4") ? "mp4" : "webm" };
}
