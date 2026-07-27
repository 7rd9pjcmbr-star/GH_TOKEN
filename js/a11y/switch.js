/**
 * Switch — một công tắc: Space/Enter/nút lớn, phân biệt ngắn/dài.
 */
(function (global) {
  "use strict";

  const state = {
    pressing: false,
    pressStart: 0,
    ignoreClickUntil: 0,
  };

  function isTypingTarget(el) {
    if (!el) return false;
    const tag = el.tagName;
    if (tag === "TEXTAREA" || tag === "SELECT") return true;
    if (tag === "INPUT") {
      const type = (el.type || "").toLowerCase();
      return !["checkbox", "radio", "button", "submit", "reset"].includes(type);
    }
    return el.isContentEditable;
  }

  function longMs() {
    return global.MaMoA11y.core.get("store")?.get("longPressMs") || 400;
  }

  function down(source) {
    if (state.pressing) return;
    state.pressing = true;
    state.pressStart = performance.now();
    document.body.classList.add("switch-down");
    global.MaMoA11y.core.get("speech")?.ensureAudio?.();
    global.MaMoA11y.core.emit("switch:down", { source });
  }

  function up(source) {
    if (!state.pressing) return;
    state.pressing = false;
    document.body.classList.remove("switch-down");
    const held = performance.now() - state.pressStart;
    state.ignoreClickUntil = performance.now() + 350;
    const kind = held >= longMs() ? "long" : "short";
    global.MaMoA11y.core.emit("switch:up", { source, held, kind });
    global.MaMoA11y.core.emit(`switch:${kind}`, { source, held });
  }

  const SwitchMod = {
    getState: () => ({ ...state }),

    bindButton(btn) {
      if (!btn) return;
      btn.addEventListener("pointerdown", (e) => {
        btn.setPointerCapture?.(e.pointerId);
        e.preventDefault();
        down("button");
      });
      btn.addEventListener("pointerup", (e) => {
        e.preventDefault();
        up("button");
      });
      btn.addEventListener("pointercancel", () => up("button"));
      btn.addEventListener("click", (e) => {
        if (performance.now() < state.ignoreClickUntil) {
          e.preventDefault();
          e.stopPropagation();
        }
      });
    },

    start() {
      window.addEventListener(
        "keydown",
        (e) => {
          if (isTypingTarget(e.target)) return;
          if (e.code === "Space" || e.code === "Enter") {
            if (e.repeat) return;
            e.preventDefault();
            down("keyboard");
          }
        },
        true
      );
      window.addEventListener(
        "keyup",
        (e) => {
          if (isTypingTarget(e.target)) return;
          if (e.code === "Space" || e.code === "Enter") {
            e.preventDefault();
            up("keyboard");
          }
        },
        true
      );

      SwitchMod.bindButton(document.getElementById("main-switch"));
    },
  };

  global.MaMoA11y.core.register("switch", SwitchMod);
})(window);
