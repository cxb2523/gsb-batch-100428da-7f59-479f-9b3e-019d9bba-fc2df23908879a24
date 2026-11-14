// Registered before the crawler module graph loads so its `import fetch from
// "node-fetch"` resolves to the local stub instead of the real ESM package.
// The stub falls through to the real implementation unless a test registers
// globalThis.__stubFetch, so ordinary local-server tests are unaffected.
import { registerHooks } from "node:module";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "node-fetch") {
      return {
        url: new URL("./node-fetch-stub.js", import.meta.url).toString(),
        shortCircuit: true,
      };
    }
    return nextResolve(specifier, context);
  },
});
