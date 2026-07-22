/**
 * Store — lưu tuỳ chọn accessibility (localStorage).
 */
(function (global) {
  "use strict";

  const KEY = "mamo.a11y.prefs.v2";

  const DEFAULTS = {
    profile: "locked-in",
    scanSpeedMs: 2500,
    scanAnnounce: true,
    autoScan: true,
    highContrast: false,
    largeTargets: true,
    reduceMotion: false,
    voiceRate: 0.95,
    longPressMs: 400,
  };

  function load() {
    try {
      const raw = localStorage.getItem(KEY);
      if (!raw) return { ...DEFAULTS };
      return { ...DEFAULTS, ...JSON.parse(raw) };
    } catch {
      return { ...DEFAULTS };
    }
  }

  let prefs = load();

  const Store = {
    defaults: { ...DEFAULTS },

    getAll() {
      return { ...prefs };
    },

    get(key) {
      return prefs[key];
    },

    set(key, value) {
      prefs = { ...prefs, [key]: value };
      try {
        localStorage.setItem(KEY, JSON.stringify(prefs));
      } catch {
        /* private mode */
      }
      global.MaMoA11y.core.emit("prefs:changed", { key, value, prefs: { ...prefs } });
      return prefs[key];
    },

    patch( partial ) {
      Object.entries(partial).forEach(([k, v]) => Store.set(k, v));
      return Store.getAll();
    },

    reset() {
      prefs = { ...DEFAULTS };
      try {
        localStorage.setItem(KEY, JSON.stringify(prefs));
      } catch {
        /* ignore */
      }
      global.MaMoA11y.core.emit("prefs:changed", { key: "*", prefs: { ...prefs } });
      return Store.getAll();
    },

    start() {
      /* hydrate body classes from prefs */
      document.body.classList.toggle("high-contrast", !!prefs.highContrast);
      document.body.classList.toggle("large-targets", prefs.largeTargets !== false);
    },
  };

  global.MaMoA11y.core.register("store", Store);
})(window);
