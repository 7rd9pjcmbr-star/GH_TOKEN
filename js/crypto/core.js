/**
 * MaMoCrypto — core registry + event bus (module 1/9)
 */
(function (global) {
  "use strict";

  const listeners = new Map();
  const modules = new Map();
  let booted = false;

  const Core = {
    version: "1.0.0",
    name: "MaMoCrypto",

    on(event, fn) {
      if (!listeners.has(event)) listeners.set(event, new Set());
      listeners.get(event).add(fn);
      return () => listeners.get(event)?.delete(fn);
    },

    emit(event, detail) {
      const set = listeners.get(event);
      if (!set) return;
      set.forEach((fn) => {
        try {
          fn(detail);
        } catch (err) {
          console.error(`[MaMoCrypto] ${event}`, err);
        }
      });
    },

    register(name, api) {
      modules.set(name, api);
      Core.emit("module:registered", { name });
      return api;
    },

    get(name) {
      return modules.get(name);
    },

    list() {
      return [...modules.keys()];
    },

    boot(options = {}) {
      if (booted) return Core;
      booted = true;
      modules.forEach((api, name) => {
        if (typeof api.start === "function") {
          try {
            api.start(options);
          } catch (err) {
            console.error(`[MaMoCrypto] start ${name}`, err);
          }
        }
      });
      Core.emit("ready", { modules: Core.list(), options });
      return Core;
    },
  };

  global.MaMoCryptoCore = Core;
  global.MaMoCrypto = global.MaMoCrypto || { core: Core };
  global.MaMoCrypto.core = Core;
})(window);
