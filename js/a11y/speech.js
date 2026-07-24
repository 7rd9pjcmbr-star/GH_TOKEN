/**
 * Speech — TTS tiếng Việt, live region, tín hiệu âm thanh.
 */
(function (global) {
  "use strict";

  let audioCtx = null;
  let liveEl = null;

  function ensureLive() {
    if (liveEl) return liveEl;
    liveEl = document.getElementById("a11y-live");
    if (!liveEl) {
      liveEl = document.createElement("div");
      liveEl.id = "a11y-live";
      liveEl.className = "a11y-live";
      liveEl.setAttribute("aria-live", "assertive");
      liveEl.setAttribute("aria-atomic", "true");
      document.body.prepend(liveEl);
    }
    return liveEl;
  }

  function ensureAudio() {
    if (!audioCtx) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (audioCtx.state === "suspended") audioCtx.resume();
    return audioCtx;
  }

  const Speech = {
    announce(text) {
      const el = ensureLive();
      el.textContent = "";
      requestAnimationFrame(() => {
        el.textContent = text || "";
      });
    },

    speak(text, opts = {}) {
      if (!window.speechSynthesis || !text) return null;
      if (!opts.queue) window.speechSynthesis.cancel();
      const utter = new SpeechSynthesisUtterance(text);
      utter.lang = "vi-VN";
      const store = global.MaMoA11y.core.get("store");
      utter.rate = opts.rate != null ? opts.rate : store?.get("voiceRate") ?? 0.95;
      const voices = window.speechSynthesis.getVoices();
      const vi = voices.find((v) => v.lang.toLowerCase().startsWith("vi"));
      if (vi) utter.voice = vi;
      window.speechSynthesis.speak(utter);
      if (!opts.quietStatus) {
        global.MaMoA11y.core.emit("status", { text: "Đang đọc…" });
      }
      utter.onend = () => {
        if (!opts.quietStatus) {
          global.MaMoA11y.core.emit("status", { text: "Đã đọc xong" });
        }
        if (typeof opts.onend === "function") opts.onend();
      };
      return utter;
    },

    stop() {
      if (window.speechSynthesis) window.speechSynthesis.cancel();
      global.MaMoA11y.core.emit("status", { text: "Đã dừng" });
    },

    tone(durationMs, frequency = 680) {
      const ctx = ensureAudio();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = frequency;
      osc.type = "sine";
      gain.gain.value = 0.0001;
      osc.connect(gain);
      gain.connect(ctx.destination);
      const now = ctx.currentTime;
      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.exponentialRampToValueAtTime(0.18, now + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + durationMs / 1000);
      osc.start(now);
      osc.stop(now + durationMs / 1000 + 0.02);
    },

    blip(freq = 520) {
      Speech.tone(55, freq);
    },

    ensureAudio,

    start() {
      ensureLive();
      if (window.speechSynthesis) {
        window.speechSynthesis.getVoices();
        window.speechSynthesis.onvoiceschanged = () =>
          window.speechSynthesis.getVoices();
      }
    },
  };

  global.MaMoA11y.core.register("speech", Speech);
})(window);
