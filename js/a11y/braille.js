/**
 * Braille — grade 1 encode/decode + bàn phím 6 điểm.
 */
(function (global) {
  "use strict";

  const BRAILLE_ALPHA = {
    a: "⠁",
    b: "⠃",
    c: "⠉",
    d: "⠙",
    e: "⠑",
    f: "⠋",
    g: "⠛",
    h: "⠓",
    i: "⠊",
    j: "⠚",
    k: "⠅",
    l: "⠇",
    m: "⠍",
    n: "⠝",
    o: "⠕",
    p: "⠏",
    q: "⠟",
    r: "⠗",
    s: "⠎",
    t: "⠞",
    u: "⠥",
    v: "⠧",
    w: "⠺",
    x: "⠭",
    y: "⠽",
    z: "⠵",
    " ": "⠀",
    ",": "⠂",
    ";": "⠆",
    ":": "⠒",
    ".": "⠲",
    "!": "⠖",
    "?": "⠦",
    "'": "⠄",
    "-": "⠤",
  };

  const BRAILLE_NUM = {
    1: "⠁",
    2: "⠃",
    3: "⠉",
    4: "⠙",
    5: "⠑",
    6: "⠋",
    7: "⠛",
    8: "⠓",
    9: "⠊",
    0: "⠚",
  };

  const NUMBER_SIGN = "⠼";
  const CAPITAL_SIGN = "⠠";
  const LOOKUP = Object.fromEntries(
    Object.entries(BRAILLE_ALPHA).map(([ch, br]) => [br, ch])
  );

  function encode(text) {
    let out = "";
    let inNumber = false;
    const normalized = String(text || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "");
    for (const ch of normalized) {
      if (/[0-9]/.test(ch)) {
        if (!inNumber) {
          out += NUMBER_SIGN;
          inNumber = true;
        }
        out += BRAILLE_NUM[ch];
        continue;
      }
      inNumber = false;
      if (/[A-Z]/.test(ch)) {
        out += CAPITAL_SIGN + BRAILLE_ALPHA[ch.toLowerCase()];
        continue;
      }
      const lower = ch.toLowerCase();
      if (BRAILLE_ALPHA[lower] !== undefined) out += BRAILLE_ALPHA[lower];
      else if (ch === " ") out += "⠀";
      else out += ch;
    }
    return out;
  }

  function decode(raw) {
    let out = "";
    let nextCapital = false;
    let inNumber = false;
    for (const ch of [...String(raw || "")]) {
      if (ch === CAPITAL_SIGN) {
        nextCapital = true;
        continue;
      }
      if (ch === NUMBER_SIGN) {
        inNumber = true;
        continue;
      }
      if (ch === "⠀" || ch === " ") {
        out += " ";
        inNumber = false;
        nextCapital = false;
        continue;
      }
      if (inNumber) {
        const digit = Object.entries(BRAILLE_NUM).find(([, br]) => br === ch);
        if (digit) {
          out += digit[0];
          continue;
        }
        inNumber = false;
      }
      const mapped = LOOKUP[ch];
      if (mapped === undefined) out += ch;
      else if (nextCapital) {
        out += mapped.toUpperCase();
        nextCapital = false;
      } else out += mapped;
    }
    return out.trim();
  }

  function dotsToChar(activeDots) {
    let mask = 0;
    for (const d of activeDots) mask |= 1 << (d - 1);
    return String.fromCodePoint(0x2800 + mask);
  }

  const Braille = {
    encode,
    decode,

    start() {
      const input = document.getElementById("braille-input");
      const dots = [...document.querySelectorAll(".dot")];

      document.getElementById("btn-decode-braille")?.addEventListener("click", () => {
        const decoded = decode(input?.value || "");
        global.MaMoA11y.setResult?.(
          decoded || "(trống)",
          decoded ? "Braille → chữ" : "Không có dữ liệu"
        );
        if (decoded) global.MaMoA11y.core.get("speech")?.speak(decoded);
      });

      document.getElementById("btn-encode-braille")?.addEventListener("click", () => {
        const text = document.getElementById("text-to-braille")?.value || "";
        const encoded = encode(text);
        if (input) input.value = encoded;
        global.MaMoA11y.setResult?.(encoded, "Chữ → Braille");
      });

      document.getElementById("btn-clear-braille")?.addEventListener("click", () => {
        if (input) input.value = "";
        const t = document.getElementById("text-to-braille");
        if (t) t.value = "";
        dots.forEach((d) => d.setAttribute("aria-pressed", "false"));
        global.MaMoA11y.setResult?.("Chưa có câu nào được chọn.", "Sẵn sàng");
      });

      dots.forEach((btn) => {
        btn.addEventListener("click", () => {
          const pressed = btn.getAttribute("aria-pressed") === "true";
          btn.setAttribute("aria-pressed", String(!pressed));
        });
      });

      document.getElementById("btn-commit-braille")?.addEventListener("click", () => {
        const active = dots
          .filter((b) => b.getAttribute("aria-pressed") === "true")
          .map((b) => Number(b.dataset.dot));
        if (input) input.value += dotsToChar(active);
        dots.forEach((d) => d.setAttribute("aria-pressed", "false"));
      });

      document.getElementById("btn-space-braille")?.addEventListener("click", () => {
        if (input) input.value += "⠀";
      });

      document.getElementById("btn-back-braille")?.addEventListener("click", () => {
        if (!input) return;
        const chars = [...input.value];
        chars.pop();
        input.value = chars.join("");
      });
    },
  };

  global.MaMoA11y.core.register("braille", Braille);
})(window);
