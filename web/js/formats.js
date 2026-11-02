// Output formats (sizes for YouTube and Instagram), render quality and frame rates. As formats.py.

import { pyround } from "./rand.js";

const F = (key, group, name, width, height, note = "") => ({ key, group, name, width, height, note,
  label: `${group}: ${name} (${width}x${height})` });

export const FORMATS = [
  F("youtube-720", "YouTube", "720p", 1280, 720),
  F("youtube-1080", "YouTube", "1080p HD", 1920, 1080, "The usual YouTube size."),
  F("youtube-1440", "YouTube", "1440p", 2560, 1440, "Slow on a phone."),
  F("youtube-4k", "YouTube", "4K", 3840, 2160, "Very slow on a phone, and some can't encode it."),
  F("youtube-shorts", "YouTube", "Shorts (vertical)", 1080, 1920, "Shorts can be up to 3 minutes long."),
  F("instagram-reel", "Instagram", "Reel / Story (9:16)", 1080, 1920, "Reels can be up to 3 minutes long."),
  F("instagram-portrait", "Instagram", "Feed portrait (4:5)", 1080, 1350, "Takes the most room in the feed."),
  F("instagram-square", "Instagram", "Feed square (1:1)", 1080, 1080),
  F("instagram-landscape", "Instagram", "Feed landscape (1.91:1)", 1080, 566),
  F("tiktok", "Other", "TikTok (9:16)", 1080, 1920),
  F("small", "Other", "Small preview", 640, 360, "Quick to render, for checking the edit."),
];
export const BY_KEY = Object.fromEntries(FORMATS.map(f => [f.key, f]));

// Quality: [label, bits per pixel per frame].
export const QUALITY = {
  draft: ["Draft (small file)", 0.06],
  good: ["Good", 0.13],
  best: ["Best (big file)", 0.2],
};

export const FRAME_RATES = [24, 25, 30, 50, 60];

// Shard detail: the short side of the stored shard frames. Phones keep them small.
export const DETAIL = { 360: "Draft (360)", 540: "Standard (540)", 720: "High (720)" };

export function even(x) {
  return Math.max(16, pyround(x / 2) * 2);
}

export function outputSize(output) {
  const f = BY_KEY[output.format];
  if (f) return [f.width, f.height];
  return [even(output.width || 1920), even(output.height || 1080)];
}

export function bitrate(width, height, fps, quality) {
  const bpp = (QUALITY[quality] || QUALITY.good)[1];
  return Math.round(Math.max(1_000_000, width * height * fps * bpp));
}

// Shard frame size for an output shape: the output's aspect, short side detail (never above the output).
export function poolSize(width, height, detail) {
  const short = Math.min(detail, Math.min(width, height));
  if (width >= height) return [even(short * width / height), even(short)];
  return [even(short), even(short * height / width)];
}

export function previewSize(width, height, longest = 960) {
  const scale = Math.min(1, longest / Math.max(width, height));
  return [even(width * scale), even(height * scale)];
}
