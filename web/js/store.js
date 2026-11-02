// Everything kept on the phone, in the browser's IndexedDB: projects, their media files, the cut shards,
// the song analysis and finished videos.

const NAME = "shardloom";
const STORES = ["projects", "files", "pools", "frames", "analyses", "renders"];
let opened = null;

function db() {
  if (!opened) {
    opened = new Promise((resolve, reject) => {
      const req = indexedDB.open(NAME, 1);
      req.onupgradeneeded = () => {
        for (const s of STORES) if (!req.result.objectStoreNames.contains(s)) req.result.createObjectStore(s);
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }
  return opened;
}

const done = req => new Promise((resolve, reject) => {
  req.onsuccess = () => resolve(req.result);
  req.onerror = () => reject(req.error);
});

async function tx(store, mode, work) {
  const d = await db();
  const t = d.transaction(store, mode);
  const result = await work(t.objectStore(store));
  await new Promise((resolve, reject) => {
    t.oncomplete = resolve;
    t.onerror = () => reject(t.error);
    t.onabort = () => reject(t.error || new Error("storage aborted (is the phone full?)"));
  });
  return result;
}

export const get = (store, key) => tx(store, "readonly", s => done(s.get(key)));
export const put = (store, key, value) => tx(store, "readwrite", s => done(s.put(value, key)));
export const del = (store, key) => tx(store, "readwrite", s => done(s.delete(key)));

export const all = store => tx(store, "readonly", async s => {
  const [keys, values] = await Promise.all([done(s.getAllKeys()), done(s.getAll())]);
  return keys.map((k, i) => [k, values[i]]);
});

// Deletes every key starting with prefix.
export const delPrefix = (store, prefix) => tx(store, "readwrite", s =>
  done(s.delete(IDBKeyRange.bound(prefix, prefix + "￿"))));

export const keysWithPrefix = (store, prefix) => tx(store, "readonly", s =>
  done(s.getAllKeys(IDBKeyRange.bound(prefix, prefix + "￿"))));

// Asks the browser not to clear the projects when the phone runs low on space.
export async function persist() {
  try {
    if (navigator.storage && navigator.storage.persist) return await navigator.storage.persist();
  } catch { /* not offered */ }
  return false;
}

export async function usage() {
  try {
    if (navigator.storage && navigator.storage.estimate) return await navigator.storage.estimate();
  } catch { /* not offered */ }
  return null;
}

// A project and everything belonging to it.
export async function deleteProject(id) {
  await del("projects", id);
  await del("pools", id);
  await del("analyses", id);
  await delPrefix("files", `${id}/`);
  await delPrefix("frames", `${id}/`);
  for (const [k, r] of await all("renders")) if (r.projectId === id) await del("renders", k);
}
