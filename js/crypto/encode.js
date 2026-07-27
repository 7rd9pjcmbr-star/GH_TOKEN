/**
 * Encode — tiện ích mã hoá biểu diễn giáo dục (module 8/9)
 * Morse / Braille / Base64 — KHÔNG phải mật mã bảo mật.
 */
(function (global) {
  "use strict";

  const MORSE = {
    A: ".-",
    B: "-...",
    C: "-.-.",
    D: "-..",
    E: ".",
    F: "..-.",
    G: "--.",
    H: "....",
    I: "..",
    J: ".---",
    K: "-.-",
    L: ".-..",
    M: "--",
    N: "-.",
    O: "---",
    P: ".--.",
    Q: "--.-",
    R: ".-.",
    S: "...",
    T: "-",
    U: "..-",
    V: "...-",
    W: ".--",
    X: "-..-",
    Y: "-.--",
    Z: "--..",
  };
  const MORSE_REV = Object.fromEntries(
    Object.entries(MORSE).map(([k, v]) => [v, k])
  );

  const BRAILLE = {
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
  };
  const BRAILLE_REV = Object.fromEntries(
    Object.entries(BRAILLE).map(([k, v]) => [v, k])
  );

  function strip(text) {
    return String(text || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "");
  }

  const Encode = {
    disclaimer:
      "Các hàm encode/* chỉ đổi dạng biểu diễn (Morse/Braille/Base64), không cung cấp bảo mật mật mã.",

    toMorse(text) {
      return strip(text)
        .toUpperCase()
        .split(/\s+/)
        .filter(Boolean)
        .map((w) =>
          [...w]
            .map((ch) => MORSE[ch] || "")
            .filter(Boolean)
            .join(" ")
        )
        .join(" / ");
    },

    fromMorse(code) {
      return String(code || "")
        .trim()
        .split(/\s*\/\s*/)
        .map((word) =>
          word
            .trim()
            .split(/\s+/)
            .map((t) => MORSE_REV[t] || "�")
            .join("")
        )
        .join(" ");
    },

    toBraille(text) {
      return [...strip(text).toLowerCase()]
        .map((ch) => BRAILLE[ch] || ch)
        .join("");
    },

    fromBraille(code) {
      return [...String(code || "")]
        .map((ch) => BRAILLE_REV[ch] || (ch === "⠀" ? " " : ch))
        .join("")
        .trim();
    },

    toBase64(text) {
      try {
        return btoa(unescape(encodeURIComponent(String(text || ""))));
      } catch {
        return "";
      }
    },

    fromBase64(b64) {
      try {
        return decodeURIComponent(escape(atob(String(b64 || ""))));
      } catch {
        return "";
      }
    },

    explain(kind) {
      const map = {
        morse: "Morse: tín hiệu chấm/gạch — hỗ trợ giao tiếp, không phải encryption.",
        braille: "Braille grade 1 Unicode — trợ năng khiếm thị.",
        base64: "Base64: biểu diễn nhị phân bằng text — ai cũng giải được.",
      };
      return map[kind] || Encode.disclaimer;
    },

    start() {},
  };

  global.MaMoCryptoCore.register("encode", Encode);
})(window);
