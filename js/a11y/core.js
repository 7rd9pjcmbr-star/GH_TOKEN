/**
 * Mã Mở A11y — Core
 * Event bus + module registry + bootstrap lifecycle.
 */
(function (global) {
  "use strict";

  const listeners = new Map();
  const modules = new Map();
  let booted = false;

  const Core = {
    version: "2.0.0",

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
          console.error(`[MaMo.core] ${event}`, err);
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

    /** Gọi start() trên mọi module đã đăng ký (nếu có). */
    boot(options = {}) {
      if (booted) return;
      booted = true;
      modules.forEach((api, name) => {
        if (typeof api.start === "function") {
          try {
            api.start(options);
          } catch (err) {
            console.error(`[MaMo.core] start ${name}`, err);
          }
        }
      });
      Core.emit("app:ready", { modules: Core.list(), options });
    },
  };

  global.MaMoA11y = global.MaMoA11y || {};
  global.MaMoA11y.core = Core;
  global.MaMo = global.MaMo || {};
  global.MaMo.core = Core;
})(window);
