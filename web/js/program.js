// The finished video's timeline, a port of compose.Program: the song section (trimmed), titles over it or on
// their own cards before and after, and fades in and out. Program time T runs over the whole video.

const ease = a => a * a * (3 - 2 * a);

export class Placed {
  constructor(index, title, t0, t1, card) {
    Object.assign(this, { index, title, t0, t1, card });
  }

  alpha(t) {
    if (!(this.t0 <= t && t < this.t1)) return 0;
    const fade = Math.min(Math.max(0, this.title.fade), (this.t1 - this.t0) / 2);
    if (fade <= 0) return 1;
    return ease(Math.min((t - this.t0) / fade, (this.t1 - t) / fade, 1));
  }
}

export class Program {
  constructor(project, songDuration, { start = null, end = null, titles = true } = {}) {
    const out = project.output;
    start = start === null ? Number(out.start || 0) : start;
    end = end === null ? out.end : end;
    end = end === null || end === undefined || end <= 0 ? songDuration : songDuration ? Math.min(Number(end), songDuration) : end;
    start = Math.max(0, Math.min(start, end));
    if (end <= start) throw new Error("nothing to render: the song section is empty");
    this.songStart = start;
    this.songEnd = end;
    this.fadeIn = Math.max(0, Number(out.fade_in || 0));
    this.fadeOut = Math.max(0, Number(out.fade_out || 0));
    const length = end - start;
    const items = titles ? project.titles.map((t, i) => [i, t]) : [];
    const pre = items.filter(([, t]) => t.at === "start" && t.extra);
    const overStart = items.filter(([, t]) => t.at === "start" && !t.extra);
    const overEnd = items.filter(([, t]) => t.at === "end" && !t.extra);
    const post = items.filter(([, t]) => t.at === "end" && t.extra);
    const dur = t => Math.max(0.1, t.duration);
    this.pre = pre.reduce((a, [, t]) => a + dur(t), 0);
    this.post = post.reduce((a, [, t]) => a + dur(t), 0);
    this.duration = this.pre + length + this.post;
    this.placed = [];
    let t = 0;
    for (const [i, title] of pre) {
      this.placed.push(new Placed(i, title, t, t + dur(title), true));
      t += dur(title);
    }
    t = this.pre;
    for (const [i, title] of overStart) {
      const d = Math.min(dur(title), this.pre + length - t);
      if (d > 0) {
        this.placed.push(new Placed(i, title, t, t + d, false));
        t += d;
      }
    }
    // Titles at the end finish with the song, the last one last.
    t = this.pre + length;
    for (const [i, title] of [...overEnd].reverse()) {
      const d = Math.min(dur(title), t - this.pre);
      if (d > 0) {
        this.placed.push(new Placed(i, title, t - d, t, false));
        t -= d;
      }
    }
    t = this.pre + length;
    for (const [i, title] of post) {
      this.placed.push(new Placed(i, title, t, t + dur(title), true));
      t += dur(title);
    }
  }

  // The song time shown at program time T, or null on a card before or after the song.
  songTime(T) {
    if (this.pre <= T && T < this.pre + (this.songEnd - this.songStart)) return this.songStart + T - this.pre;
    return null;
  }

  programTime(songT) {
    return this.pre + songT - this.songStart;
  }

  titlesAt(T) {
    const out = [];
    for (const p of this.placed) {
      const a = p.alpha(T);
      if (a > 0) out.push([p, a]);
    }
    return out;
  }

  // Picture brightness for the fades at the very start and end.
  fade(T) {
    let f = 1;
    if (this.fadeIn > 0) f = Math.min(f, T / this.fadeIn);
    if (this.fadeOut > 0) f = Math.min(f, (this.duration - T) / this.fadeOut);
    return Math.min(1, Math.max(0, f));
  }

  // The soundtrack for the whole program from the song's channels (Float32Arrays at rate): the section, faded,
  // with silence under the cards.
  audio(channels, rate) {
    const a0 = Math.round(this.songStart * rate), a1 = Math.round(this.songEnd * rate);
    const pre = Math.round(this.pre * rate), post = Math.round(this.post * rate);
    const n = a1 - a0;
    const fi = Math.trunc(this.fadeIn * rate), fo = Math.trunc(this.fadeOut * rate);
    return channels.map(ch => {
      const out = new Float32Array(pre + n + post);
      out.set(ch.subarray(a0, Math.min(a1, ch.length)), pre);
      if (fi) {
        const m = Math.min(fi, n);
        for (let k = 0; k < m; k++) out[pre + k] *= m > 1 ? k / (m - 1) : 0;
      }
      if (fo) {
        const m = Math.min(fo, n);
        for (let k = 0; k < m; k++) out[pre + n - m + k] *= m > 1 ? 1 - k / (m - 1) : 0;
      }
      return out;
    });
  }
}
