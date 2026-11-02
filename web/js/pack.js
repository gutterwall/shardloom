// Packed projects: a zip of project.json and its media/ folder, the same as the desktop's File > Pack, so a
// project moves between the phone and a computer. Shards and analysis are rebuilt after opening.

import { Zip, ZipPassThrough, ZipDeflate, Unzip, UnzipInflate, strToU8, strFromU8 } from "../vendor/fflate.mjs";

async function feed(blob, push) {
  const reader = blob.stream().getReader();
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return push(new Uint8Array(0), true);
    push(value, false);
  }
}

// A zip Blob of the project and its files ([[path, Blob], ...]).
export async function pack(project, files) {
  const parts = [];
  let failure = null;
  const zip = new Zip((err, chunk) => {
    if (err) failure = err;
    else parts.push(chunk);
  });
  const json = new ZipDeflate("project.json", { level: 6 });
  zip.add(json);
  json.push(strToU8(JSON.stringify(project, null, 1)), true);
  for (const [path, blob] of files) {
    // Media is compressed already; storing it is quicker.
    const entry = new ZipPassThrough(path);
    zip.add(entry);
    if (blob.size) await feed(blob, (chunk, last) => entry.push(chunk, last));
    else entry.push(new Uint8Array(0), true);
    if (failure) throw failure;
  }
  zip.end();
  if (failure) throw failure;
  return new Blob(parts, { type: "application/zip" });
}

const safe = name => name === "project.json" || (/^media\/[^/]+$/.test(name) && !name.includes(".."));

// The project (as a dict) and its media files from a packed zip Blob.
export async function unpack(blob) {
  const files = new Map();
  const pending = [];
  const unzip = new Unzip(file => {
    if (!safe(file.name)) return;          // shards, analysis and renders are rebuilt here
    const chunks = [];
    pending.push(new Promise((resolve, reject) => {
      file.ondata = (err, chunk, final) => {
        if (err) return reject(err);
        chunks.push(chunk);
        if (final) {
          files.set(file.name, new Blob(chunks));
          resolve();
        }
      };
    }));
    file.start();
  });
  unzip.register(UnzipInflate);
  await feed(blob, (chunk, last) => unzip.push(chunk, last));
  await Promise.all(pending);
  const json = files.get("project.json");
  if (!json) throw new Error("This is not a packed Shardloom project (no project.json in it).");
  files.delete("project.json");
  const project = JSON.parse(strFromU8(new Uint8Array(await json.arrayBuffer())));
  return { project, files };
}
