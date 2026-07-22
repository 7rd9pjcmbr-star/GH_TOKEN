/**
 * Morse — bảng mã, encode/decode, Morse một công tắc.
 */
(function (global) {
  "use strict";

  const MORSE_TO_CHAR = {
    ".-": "A",
    "-...": "B",
    "-.-.": "C",
    "-..": "D",
    ".": "E",
    "..-.": "F",
    "--.": "G",
    "....": "H",
    "..": "I",
    ".---": "J",
    "-.-": "K",
    ".-..": "L",
    "--": "M",
    "-.": "N",
    "---": "O",
    ".--.": "P",
    "--.-": "Q",
    ".-.": "R",
    "...": "S",
    "-": "T",
    "..-": "U",
    "...-": "V",
    ".--": "W",
    "-..-": "X",
    "-.--": "Y",
    "--..": "Z",
    "-----": "0",
    ".----": "1",
    "..---": "2",
    "...--": "3",
    "....-": "4",
    ".....": "5",
    "-....": "6",
    "--...": "7",
    "---..": "8",
    "----.": "9",
    ".-.-.-": ".",
    "--..--": ",",
    "..--..": "?",
    "-.-.--": "!",
  };

  const CHAR_TO_MORSE = Object.fromEntries(
    Object.entries(MORSE_TO_CHAR).map(([k, v]) => [v, k])
  );

  const LETTER_GAP_MS = 900;
  const WORD_GAP_MS = 1800;

  const buf = {
    morseBuffer: "",
    currentToken: "",
    letterTimer: null,
  };

  function normalizeToken(token) {
    return token.replace(/[·•]/g, ".").replace(/[−–—_]/g, "-").trim();
  }

  function decode(raw) {
    const cleaned = String(raw || "")
      .replace(/[·•]/g, ".")
      .replace(/[−–—_]/g, "-")
      .trim();
    if (!cleaned) return "";
    return cleaned
      .split(/\s*\/\s*|\s{2,}/)
      .map((word) =>
        word
          .trim()
          .split(/\s+/)
          .map((token) => {
            const key = normalizeToken(token);
            return key ? MORSE_TO_CHAR[key] || "�" : "";
          })
          .join("")
      )
      .join(" ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function encode(text) {
    return String(text || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toUpperCase()
      .split(/\s+/)
      .filter(Boolean)
      .map((word) =>
        [...word]
          .map((ch) => CHAR_TO_MORSE[ch] || "")
          .filter(Boolean)
          .join(" ")
      )
      .join(" / ");
  }

  function renderLive() {
    const el = document.getElementById("switch-morse-live");
    if (!el) return;
    const full = `${buf.morseBuffer}${
      buf.currentToken ? (buf.morseBuffer ? " " : "") + buf.currentToken : ""
    }`.trim();
    el.textContent = `Morse: ${full || "(trống)"}`;
    const input = document.getElementById("morse-input");
    if (input) input.value = full;
  }

  function scheduleGap() {
    if (buf.letterTimer) clearTimeout(buf.letterTimer);
    buf.letterTimer = setTimeout(() => {
      if (buf.currentToken) {
        buf.morseBuffer = `${buf.morseBuffer} ${buf.currentToken}`.trim();
        buf.currentToken = "";
        renderLive();
        global.MaMoA11y.core.get("speech")?.blip(360);
      }
      buf.letterTimer = setTimeout(() => {
        if (buf.morseBuffer && !/[/\s]$/.test(buf.morseBuffer)) {
          buf.morseBuffer = `${buf.morseBuffer} / `;
          renderLive();
        }
      }, WORD_GAP_MS - LETTER_GAP_MS);
    }, LETTER_GAP_MS);
  }

  function addSymbol(sym) {
    buf.currentToken += sym;
    renderLive();
    const speech = global.MaMoA11y.core.get("speech");
    if (sym === ".") speech?.tone(70, 700);
    else speech?.tone(210, 520);
    scheduleGap();
  }

  const Morse = {
    MORSE_TO_CHAR,
    decode,
    encode,

    clearSwitch() {
      buf.morseBuffer = "";
      buf.currentToken = "";
      if (buf.letterTimer) clearTimeout(buf.letterTimer);
      renderLive();
      global.MaMoA11y.core.emit("status", { text: "Đã xóa Morse" });
      global.MaMoA11y.core.get("speech")?.speak("Đã xóa Morse", {
        quietStatus: true,
      });
    },

    decodeSwitch() {
      if (buf.currentToken) {
        buf.morseBuffer = `${buf.morseBuffer} ${buf.currentToken}`.trim();
        buf.currentToken = "";
      }
      const decoded = decode(buf.morseBuffer);
      renderLive();
      if (decoded) {
        global.MaMoA11y.setResult?.(decoded, "Morse một công tắc");
        global.MaMoA11y.core.get("speech")?.speak(decoded);
      } else {
        global.MaMoA11y.core.get("speech")?.speak("Chưa có mã Morse", {
          quietStatus: true,
        });
        global.MaMoA11y.core.emit("status", { text: "Morse trống" });
      }
    },

    start() {
      global.MaMoA11y.core.on("morse:dot", () => addSymbol("."));
      global.MaMoA11y.core.on("morse:dash", () => addSymbol("-"));
      global.MaMoA11y.core.on("action:decode-morse", () => Morse.decodeSwitch());
      global.MaMoA11y.core.on("action:clear-morse", () => Morse.clearSwitch());

      document
        .getElementById("btn-decode-switch-morse")
        ?.addEventListener("click", () => Morse.decodeSwitch());
      document
        .getElementById("btn-clear-switch-morse")
        ?.addEventListener("click", () => Morse.clearSwitch());

      // Classic Morse panel
      const input = document.getElementById("morse-input");
      document.getElementById("btn-decode-morse")?.addEventListener("click", () => {
        const decoded = decode(input?.value || "");
        global.MaMoA11y.setResult?.(decoded || "(trống)", decoded ? "Morse → chữ" : "Không có dữ liệu");
        if (decoded) global.MaMoA11y.core.get("speech")?.speak(decoded);
      });
      document.getElementById("btn-encode-morse")?.addEventListener("click", () => {
        const text = document.getElementById("text-to-morse")?.value || "";
        const encoded = encode(text);
        if (input) input.value = encoded;
        global.MaMoA11y.setResult?.(encoded, "Chữ → Morse");
      });
      document.getElementById("btn-clear-morse")?.addEventListener("click", () => {
        if (input) input.value = "";
        const t = document.getElementById("text-to-morse");
        if (t) t.value = "";
        global.MaMoA11y.setResult?.("Chưa có câu nào được chọn.", "Sẵn sàng");
      });
      document.getElementById("tap-dot")?.addEventListener("click", () => {
        if (input) input.value += ".";
      });
      document.getElementById("tap-dash")?.addEventListener("click", () => {
        if (input) input.value += "-";
      });
      document.getElementById("tap-letter")?.addEventListener("click", () => {
        if (input && input.value && !/\s$/.test(input.value)) input.value += " ";
      });
      document.getElementById("tap-word")?.addEventListener("click", () => {
        if (!input) return;
        const v = input.value.trimEnd();
        input.value = v ? `${v} / ` : "";
      });
      document.getElementById("tap-back")?.addEventListener("click", () => {
        if (input) input.value = input.value.slice(0, -1);
      });

      renderLive();
    },
  };

  global.MaMoA11y.core.register("morse", Morse);
})(window);
