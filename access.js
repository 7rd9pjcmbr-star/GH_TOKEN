(() => {
  "use strict";

  const LONG_MS = 400;
  const LETTER_GAP_MS = 900;
  const WORD_GAP_MS = 1800;

  const ui = {
    switchBtn: document.getElementById("main-switch"),
    scanIndicator: document.getElementById("scan-indicator"),
    btnToggleScan: document.getElementById("btn-toggle-scan"),
    btnPauseScan: document.getElementById("btn-pause-scan"),
    scanSpeed: document.getElementById("scan-speed"),
    scanAnnounce: document.getElementById("scan-announce"),
    highContrast: document.getElementById("high-contrast"),
    autoScan: document.getElementById("auto-scan"),
    morseLive: document.getElementById("switch-morse-live"),
    btnDecodeMorse: document.getElementById("btn-decode-switch-morse"),
    btnClearMorse: document.getElementById("btn-clear-switch-morse"),
  };

  const state = {
    scanning: false,
    index: -1,
    timer: null,
    targets: [],
    pressStart: 0,
    pressing: false,
    morseBuffer: "",
    currentToken: "",
    letterTimer: null,
    ignoreClickUntil: 0,
  };

  function ready() {
    return window.MaMo;
  }

  function collectTargets() {
    const panel = document.querySelector('[data-panel="special"]:not([hidden])');
    if (!panel) return [];
    return [...panel.querySelectorAll(".scan-target")].filter(
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
    if (!ui.scanIndicator) return;
    if (!state.scanning) {
      ui.scanIndicator.textContent = "Quét: tắt";
      return;
    }
    const t = state.targets[state.index];
    const label = t?.dataset.scanLabel || t?.textContent || "…";
    ui.scanIndicator.textContent = `Quét: ${label}`;
  }

  function focusIndex(i) {
    clearHighlight();
    if (!state.targets.length) return;
    state.index = ((i % state.targets.length) + state.targets.length) % state.targets.length;
    const el = state.targets[state.index];
    el.classList.add("is-scan-focus");
    el.setAttribute("aria-current", "true");
    try {
      el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } catch {
      /* ignore */
    }
    updateIndicator();

    const api = ready();
    if (api) api.blip(480 + (state.index % 5) * 30);
    if (ui.scanAnnounce?.checked && api) {
      const label = el.dataset.scanLabel || el.textContent;
      api.announce(label);
      api.speak(label, { quietStatus: true, rate: 1.05 });
    }
  }

  function stopScan() {
    state.scanning = false;
    if (state.timer) {
      clearInterval(state.timer);
      state.timer = null;
    }
    clearHighlight();
    updateIndicator();
  }

  function startScan() {
    const api = ready();
    state.targets = collectTargets();
    if (!state.targets.length) {
      stopScan();
      return;
    }
    state.scanning = true;
    const speed = Number(ui.scanSpeed?.value || 2500);
    if (state.timer) clearInterval(state.timer);
    focusIndex(0);
    state.timer = setInterval(() => {
      if (!state.scanning) return;
      state.targets = collectTargets();
      if (!state.targets.length) {
        stopScan();
        return;
      }
      focusIndex(state.index + 1);
    }, speed);
    if (api) api.setStatus("Đang quét…");
  }

  function toggleScan(force) {
    const next = force != null ? force : !state.scanning;
    if (next) startScan();
    else stopScan();
    if (ui.autoScan) ui.autoScan.checked = next;
  }

  function activateFocused() {
    const el = state.targets[state.index];
    if (!el) return;
    const api = ready();
    if (api) api.blip(720);

    if (el.dataset.phrase) {
      api?.sayPhrase(el.dataset.phrase);
      return;
    }

    if (el.id === "btn-decode-switch-morse") {
      decodeSwitchMorse();
      return;
    }
    if (el.id === "btn-clear-switch-morse") {
      clearSwitchMorse();
      return;
    }
    if (el.id === "btn-pause-scan") {
      toggleScan(false);
      api?.speak("Đã tạm dừng quét", { quietStatus: true });
      return;
    }

    el.click();
  }

  function renderMorseLive() {
    if (!ui.morseLive) return;
    const full = `${state.morseBuffer}${state.currentToken ? (state.morseBuffer ? " " : "") + state.currentToken : ""}`.trim();
    ui.morseLive.textContent = `Morse: ${full || "(trống)"}`;
  }

  function scheduleLetterGap() {
    if (state.letterTimer) clearTimeout(state.letterTimer);
    state.letterTimer = setTimeout(() => {
      if (state.currentToken) {
        state.morseBuffer = `${state.morseBuffer} ${state.currentToken}`.trim();
        state.currentToken = "";
        renderMorseLive();
        ready()?.blip(360);
      }
      state.letterTimer = setTimeout(() => {
        if (state.morseBuffer && !/[/\s]$/.test(state.morseBuffer)) {
          state.morseBuffer = `${state.morseBuffer} / `;
          renderMorseLive();
        }
      }, WORD_GAP_MS - LETTER_GAP_MS);
    }, LETTER_GAP_MS);
  }

  function addMorseSymbol(sym) {
    state.currentToken += sym;
    renderMorseLive();
    const api = ready();
    if (sym === ".") api?.tone(70, 700);
    else api?.tone(210, 520);
    scheduleLetterGap();

    // mirror into classic morse field when present
    const input = api?.getMorseInput?.();
    if (input) {
      input.value = `${state.morseBuffer}${state.currentToken ? (state.morseBuffer ? " " : "") + state.currentToken : ""}`.trim();
    }
  }

  function decodeSwitchMorse() {
    const api = ready();
    if (!api) return;
    if (state.currentToken) {
      state.morseBuffer = `${state.morseBuffer} ${state.currentToken}`.trim();
      state.currentToken = "";
    }
    const decoded = api.decodeMorse(state.morseBuffer);
    if (decoded) {
      api.setResult(decoded, "Morse một công tắc");
      api.speak(decoded);
    } else {
      api.speak("Chưa có mã Morse", { quietStatus: true });
      api.setStatus("Morse trống");
    }
    renderMorseLive();
  }

  function clearSwitchMorse() {
    state.morseBuffer = "";
    state.currentToken = "";
    if (state.letterTimer) clearTimeout(state.letterTimer);
    renderMorseLive();
    const api = ready();
    const input = api?.getMorseInput?.();
    if (input) input.value = "";
    api?.setStatus("Đã xóa Morse");
    api?.speak("Đã xóa Morse", { quietStatus: true });
  }

  /**
   * Short press while scanning → select highlighted.
   * Long press → Morse dash (also usable when scan off).
   * Short press when scan off → Morse dot OR select if something focused.
   */
  function onSwitchDown(e) {
    if (e) {
      e.preventDefault();
      e.stopPropagation();
    }
    if (state.pressing) return;
    state.pressing = true;
    state.pressStart = performance.now();
    ready()?.ensureAudio?.();
    document.body.classList.add("switch-down");
  }

  function onSwitchUp(e) {
    if (e) {
      e.preventDefault();
      e.stopPropagation();
    }
    if (!state.pressing) return;
    state.pressing = false;
    document.body.classList.remove("switch-down");
    const held = performance.now() - state.pressStart;
    state.ignoreClickUntil = performance.now() + 350;

    const mode = ready()?.getMode?.() || "special";

    if (held >= LONG_MS) {
      // Long = Morse dash always available in special mode
      if (mode === "special") addMorseSymbol("-");
      return;
    }

    // Short press
    if (mode === "special" && state.scanning && state.targets[state.index]) {
      activateFocused();
      return;
    }

    if (mode === "special") {
      // If not scanning, short = Morse dot (single-switch typing)
      addMorseSymbol(".");
    }
  }

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

  // Giant switch button
  ui.switchBtn?.addEventListener("pointerdown", (e) => {
    ui.switchBtn.setPointerCapture?.(e.pointerId);
    onSwitchDown(e);
  });
  ui.switchBtn?.addEventListener("pointerup", onSwitchUp);
  ui.switchBtn?.addEventListener("pointercancel", onSwitchUp);
  ui.switchBtn?.addEventListener("click", (e) => {
    // Avoid double-fire after pointerup
    if (performance.now() < state.ignoreClickUntil) {
      e.preventDefault();
      e.stopPropagation();
    }
  });

  // Keyboard switch: Space / Enter
  window.addEventListener(
    "keydown",
    (e) => {
      if (isTypingTarget(e.target)) return;
      if (e.code === "Space" || e.code === "Enter") {
        if (e.repeat) return;
        onSwitchDown(e);
      }
    },
    true
  );

  window.addEventListener(
    "keyup",
    (e) => {
      if (isTypingTarget(e.target)) return;
      if (e.code === "Space" || e.code === "Enter") {
        onSwitchUp(e);
      }
    },
    true
  );

  ui.btnToggleScan?.addEventListener("click", () => toggleScan());
  ui.btnPauseScan?.addEventListener("click", () => toggleScan(false));

  ui.btnDecodeMorse?.addEventListener("click", decodeSwitchMorse);
  ui.btnClearMorse?.addEventListener("click", clearSwitchMorse);

  ui.scanSpeed?.addEventListener("change", () => {
    if (state.scanning) startScan();
  });

  ui.autoScan?.addEventListener("change", () => {
    toggleScan(ui.autoScan.checked);
  });

  ui.highContrast?.addEventListener("change", () => {
    document.body.classList.toggle("high-contrast", ui.highContrast.checked);
  });

  document.addEventListener("mamo:mode", (ev) => {
    const mode = ev.detail?.mode;
    if (mode === "special") {
      if (ui.autoScan?.checked) startScan();
    } else {
      stopScan();
    }
  });

  // Boot special mode scanning
  function boot() {
    if (!ready()) {
      setTimeout(boot, 40);
      return;
    }
    renderMorseLive();
    updateIndicator();
    if (ui.autoScan?.checked && ready().getMode() === "special") {
      // slight delay so voices / layout settle
      setTimeout(() => startScan(), 600);
    }
  }

  boot();

  window.MaMoAccess = {
    startScan,
    stopScan,
    toggleScan,
    getState: () => ({ ...state, index: state.index }),
  };
})();
