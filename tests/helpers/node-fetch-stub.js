// Test stub for the ESM `node-fetch` dependency. When a test registers
// globalThis.__stubFetch, requests route through it; otherwise they fall
// through to the real node-fetch implementation so local-server tests work.
//
// The explicit file path (not the bare "node-fetch" specifier) is required so
// the resolve hook does not redirect this import back into this stub.
import realFetch from "../../node_modules/node-fetch/src/index.js";

export default async function stubFetch(url, init) {
  if (typeof globalThis.__stubFetch === "function") {
    return globalThis.__stubFetch(url, init);
  }
  return realFetch(url, init);
}
