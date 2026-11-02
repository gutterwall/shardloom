// Keeps Shardloom working offline: the app's own files are cached, everything else (your media) is local anyway.

const CACHE = "shardloom-0.6.0";
const FILES = [
  "./", "index.html", "manifest.webmanifest", "css/app.css",
  "icons/icon.svg", "icons/icon-180.png", "icons/icon-192.png", "icons/icon-512.png", "icons/icon-maskable-512.png",
  "js/app.js", "js/analysis.js", "js/analysis-worker.js", "js/audio.js", "js/engine.js", "js/events.js",
  "js/exporter.js", "js/formats.js", "js/fx.js", "js/pace.js", "js/pack.js", "js/pool.js", "js/program.js",
  "js/project.js", "js/rand.js", "js/renderer.js", "js/shaders.js", "js/store.js", "js/titles.js",
  "vendor/standalone.module.js", "vendor/mp4-muxer.mjs", "vendor/fflate.mjs",
];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// The network first, so an update shows on the next visit; the cache when offline.
self.addEventListener("fetch", e => {
  if (e.request.method !== "GET" || new URL(e.request.url).origin !== location.origin) return;
  e.respondWith(fetch(e.request).then(res => {
    if (res.ok) {
      const copy = res.clone();
      caches.open(CACHE).then(c => c.put(e.request, copy));
    }
    return res;
  }).catch(() => caches.match(e.request, { ignoreSearch: true })));
});
