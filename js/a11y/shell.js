/**
 * Shell — điều hướng chế độ, thanh kết quả, dock, bootstrap UI.
 */
(function (global) {
  "use strict";

  let currentMode = "special";

  const Shell = {
    getMode() {
      return currentMode;
    },

    setMode(mode) {
      currentMode = mode;
      document.querySelectorAll(".nav-btn[data-mode]").forEach((btn) => {
        const on = btn.dataset.mode === mode;
        btn.classList.toggle("is-active", on);
        btn.setAttribute("aria-pressed", String(on));
      });
      document.querySelectorAll("[data-panel]").forEach((panel) => {
        const on = panel.dataset.panel === mode;
        panel.hidden = !on;
        panel.classList.toggle("is-visible", on);
      });
      document.body.classList.toggle("mode-special", mode === "special");
      global.MaMoA11y.core.emit("mode:change", { mode });
    },

    start() {
      document.querySelectorAll(".nav-btn[data-mode]").forEach((btn) => {
        btn.addEventListener("click", () => Shell.setMode(btn.dataset.mode));
      });

      global.MaMoA11y.core.on("status", ({ text }) => {
        const pill = document.getElementById("status-pill");
        if (pill && text) pill.textContent = text;
      });

      document.getElementById("btn-speak")?.addEventListener("click", () => {
        const text = document.getElementById("result-text")?.textContent;
        if (
          text &&
          text !== "(trống)" &&
          text !== "Chưa có câu nào được chọn."
        ) {
          global.MaMoA11y.core.get("speech")?.speak(text);
        }
      });

      document.getElementById("btn-stop-speak")?.addEventListener("click", () => {
        global.MaMoA11y.core.get("speech")?.stop();
      });

      document.getElementById("btn-copy")?.addEventListener("click", async () => {
        const text = document.getElementById("result-text")?.textContent || "";
        try {
          await navigator.clipboard.writeText(text);
          global.MaMoA11y.core.emit("status", { text: "Đã sao chép" });
        } catch {
          global.MaMoA11y.core.emit("status", { text: "Không sao chép được" });
        }
      });

      document.getElementById("btn-demo-speak")?.addEventListener("click", () => {
        global.MaMoA11y.core.get("speech")?.speak(
          "Xin chào. Đây là Mã Mở, kiến trúc hỗ trợ đặc biệt theo module. Chọn hồ sơ phù hợp, máy sẽ quét từng câu. Khi câu bạn muốn sáng lên, nhấn công tắc Space, Enter hoặc nút lớn để đọc to."
        );
      });

      // Mark action buttons for scan activate
      const decodeBtn = document.getElementById("btn-decode-switch-morse");
      const clearBtn = document.getElementById("btn-clear-switch-morse");
      if (decodeBtn) decodeBtn.dataset.action = "decode-morse";
      if (clearBtn) clearBtn.dataset.action = "clear-morse";

      // Module map live count
      const mapEl = document.getElementById("module-map-list");
      if (mapEl) {
        const names = global.MaMoA11y.core.list();
        mapEl.innerHTML = names
          .map((n) => `<li><code>${n}</code></li>`)
          .join("");
      }

      const profile = global.MaMoA11y.core.get("store")?.get("profile") || "locked-in";
      const defMode =
        global.MaMoA11y.core.get("profiles")?.get(profile)?.defaultMode ||
        "special";
      Shell.setMode(defMode);

      if (
        defMode === "special" &&
        global.MaMoA11y.core.get("store")?.get("autoScan")
      ) {
        setTimeout(() => global.MaMoA11y.core.get("scan")?.startScan(), 700);
      }
    },
  };

  global.MaMoA11y.core.register("shell", Shell);

  // Compat bridge for legacy MaMo callers
  global.MaMo = global.MaMo || {};
  global.MaMo.switchMode = (m) => Shell.setMode(m);
  global.MaMo.getMode = () => Shell.getMode();
  global.MaMo.speak = (...args) =>
    global.MaMoA11y.core.get("speech")?.speak(...args);
  global.MaMo.sayPhrase = (t) => global.MaMoA11y.core.get("phrases")?.say(t);
  global.MaMo.decodeMorse = (t) =>
    global.MaMoA11y.core.get("morse")?.decode(t);
})(window);
