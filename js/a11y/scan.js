/**
 * Scan — quét tự động các .scan-target trong panel hiện tại.
 */
(function (global) {
  "use strict";

  const state = {
    scanning: false,
    index: -1,
    timer: null,
    targets: [],
    rootSelector: '[data-panel="special"]:not([hidden])',
  };

  function speech() {
    return global.MaMoA11y.core.get("speech");
  }

  function store() {
    return global.MaMoA11y.core.get("store");
  }

  function collect() {
    const root = document.querySelector(state.rootSelector);
    if (!root) return [];
    return [...root.querySelectorAll(".scan-target")].filter(
      (el) => !el.disabled && el.offsetParent !== null
    );
  }

  function clearHighlight() {
    document.querySelectorAll(".scan-target.is-scan-focus").forEach((el) => {
      el.classList.remove("is-scan-focus");
      el.removeAttribute("aria-current");
    });
  }

  function updateIndicator() {
    const el = document.getElementById("scan-indicator");
    if (!el) return;
    if (!state.scanning) {
      el.textContent = "Quét: tắt";
      return;
    }
    const t = state.targets[state.index];
    el.textContent = `Quét: ${t?.dataset.scanLabel || t?.textContent || "…"}`;
  }

  function focusIndex(i) {
    clearHighlight();
    if (!state.targets.length) return;
    state.index =
      ((i % state.targets.length) + state.targets.length) % state.targets.length;
    const el = state.targets[state.index];
    el.classList.add("is-scan-focus");
    el.setAttribute("aria-current", "true");
    try {
      el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } catch {
      /* ignore */
    }
    updateIndicator();
    speech()?.blip(480 + (state.index % 5) * 30);
    if (store()?.get("scanAnnounce")) {
      const label = el.dataset.scanLabel || el.textContent;
      speech()?.announce(label);
      speech()?.speak(label, { quietStatus: true, rate: 1.05 });
    }
    global.MaMoA11y.core.emit("scan:focus", { index: state.index, el });
  }

  const Scan = {
    getState: () => ({ ...state }),

    stop() {
      state.scanning = false;
      if (state.timer) {
        clearInterval(state.timer);
        state.timer = null;
      }
      clearHighlight();
      updateIndicator();
      global.MaMoA11y.core.emit("scan:stop");
    },

    startScan() {
      state.targets = collect();
      if (!state.targets.length) {
        Scan.stop();
        return;
      }
      state.scanning = true;
      const speed = store()?.get("scanSpeedMs") || 2500;
      if (state.timer) clearInterval(state.timer);
      focusIndex(0);
      state.timer = setInterval(() => {
        if (!state.scanning) return;
        state.targets = collect();
        if (!state.targets.length) {
          Scan.stop();
          return;
        }
        focusIndex(state.index + 1);
      }, speed);
      global.MaMoA11y.core.emit("status", { text: "Đang quét…" });
      global.MaMoA11y.core.emit("scan:start");
    },

    toggle(force) {
      const next = force != null ? force : !state.scanning;
      if (next) Scan.startScan();
      else Scan.stop();
      store()?.set("autoScan", next);
      const auto = document.getElementById("auto-scan");
      if (auto) auto.checked = next;
    },

    activateFocused() {
      const el = state.targets[state.index];
      if (!el) return false;
      speech()?.blip(720);
      global.MaMoA11y.core.emit("scan:activate", { el, index: state.index });

      if (el.dataset.phrase) {
        global.MaMoA11y.core.get("phrases")?.say(el.dataset.phrase);
        return true;
      }
      if (el.dataset.action) {
        global.MaMoA11y.core.emit(`action:${el.dataset.action}`);
        return true;
      }
      el.click();
      return true;
    },

    start() {
      global.MaMoA11y.core.on("switch:short", () => {
        const shell = global.MaMoA11y.core.get("shell");
        if (shell?.getMode() !== "special") return;
        if (state.scanning && state.targets[state.index]) {
          Scan.activateFocused();
        } else {
          global.MaMoA11y.core.emit("morse:dot");
        }
      });

      global.MaMoA11y.core.on("switch:long", () => {
        const shell = global.MaMoA11y.core.get("shell");
        if (shell?.getMode() !== "special") return;
        global.MaMoA11y.core.emit("morse:dash");
      });

      global.MaMoA11y.core.on("mode:change", ({ mode }) => {
        if (mode === "special" && store()?.get("autoScan")) {
          setTimeout(() => Scan.startScan(), 400);
        } else {
          Scan.stop();
        }
      });

      document.getElementById("btn-toggle-scan")?.addEventListener("click", () =>
        Scan.toggle()
      );
      document.getElementById("btn-pause-scan")?.addEventListener("click", () =>
        Scan.toggle(false)
      );
    },
  };

  global.MaMoA11y.core.register("scan", Scan);
})(window);
