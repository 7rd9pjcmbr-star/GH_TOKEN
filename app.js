(() => {
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
    "-....-": "-",
    "-..-.": "/",
    ".----.": "'",
    "-.--.": "(",
    "-.--.-": ")",
    ".-...": "&",
    "---...": ":",
    "-.-.-.": ";",
    "-...-": "=",
    ".-.-.": "+",
    "..--.-": "_",
    ".-..-.": '"',
    "...-..-": "$",
    ".--.-.": "@",
  };

  const CHAR_TO_MORSE = Object.fromEntries(
    Object.entries(MORSE_TO_CHAR).map(([k, v]) => [v, k])
  );

  // Grade-1 Braille (Unicode U+2800 block) for A–Z, 0–9, space, punctuation
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
    "(": "⠶",
    ")": "⠶",
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

  const CHAR_TO_BRAILLE_LOOKUP = Object.fromEntries(
    Object.entries(BRAILLE_ALPHA).map(([ch, br]) => [br, ch])
  );

  const PHRASES = [
    "Xin chào",
    "Cảm ơn",
    "Tôi cần giúp đỡ",
    "Tôi đói",
    "Tôi khát",
    "Tôi đau",
    "Đồng ý",
    "Không",
    "Xin chờ một chút",
    "Gọi người thân",
    "Tôi ổn",
    "Xin lỗi",
  ];

  const el = {
    navBtns: [...document.querySelectorAll(".nav-btn")],
    panels: [...document.querySelectorAll("[data-panel]")],
    morseInput: document.getElementById("morse-input"),
    textToMorse: document.getElementById("text-to-morse"),
    brailleInput: document.getElementById("braille-input"),
    textToBraille: document.getElementById("text-to-braille"),
    resultText: document.getElementById("result-text"),
    statusPill: document.getElementById("status-pill"),
    btnSpeak: document.getElementById("btn-speak"),
    btnCopy: document.getElementById("btn-copy"),
    btnStop: document.getElementById("btn-stop-speak"),
    btnPlayMorse: document.getElementById("btn-play-morse"),
    phraseBoard: document.getElementById("phrase-board"),
    dots: [...document.querySelectorAll(".dot")],
  };

  let lastMorseCode = "";
  let audioCtx = null;

  function setStatus(text) {
    el.statusPill.textContent = text;
  }

  function setResult(text, status = "Đã giải mã") {
    const value = (text || "").trim() || "(trống)";
    el.resultText.textContent = value;
    setStatus(status);
    const usable = value !== "(trống)";
    el.btnSpeak.disabled = !usable;
    el.btnCopy.disabled = !usable;
  }

  function normalizeMorseToken(token) {
    return token
      .replace(/[·•]/g, ".")
      .replace(/[−–—_]/g, "-")
      .trim();
  }

  function decodeMorse(raw) {
    const cleaned = raw
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
            const key = normalizeMorseToken(token);
            if (!key) return "";
            return MORSE_TO_CHAR[key] || "�";
          })
          .join("")
      )
      .join(" ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function encodeMorse(text) {
    return text
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

  function encodeBraille(text) {
    let out = "";
    let inNumber = false;
    const normalized = text.normalize("NFD").replace(/[\u0300-\u036f]/g, "");

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
      if (BRAILLE_ALPHA[lower] !== undefined) {
        out += BRAILLE_ALPHA[lower];
      } else if (ch === " ") {
        out += "⠀";
      } else {
        out += ch;
      }
    }
    return out;
  }

  function decodeBraille(raw) {
    let out = "";
    let nextCapital = false;
    let inNumber = false;
    const chars = [...raw];

    for (let i = 0; i < chars.length; i += 1) {
      const ch = chars[i];
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
        const digitEntry = Object.entries(BRAILLE_NUM).find(([, br]) => br === ch);
        if (digitEntry) {
          out += digitEntry[0];
          continue;
        }
        inNumber = false;
      }

      const mapped = CHAR_TO_BRAILLE_LOOKUP[ch];
      if (mapped === undefined) {
        out += ch;
        continue;
      }
      if (mapped === " ") {
        out += " ";
      } else if (nextCapital) {
        out += mapped.toUpperCase();
        nextCapital = false;
      } else {
        out += mapped;
      }
    }
    return out.trim();
  }

  function dotsToBrailleChar(activeDots) {
    // Unicode Braille: bit0=dot1, bit1=dot2, bit2=dot3, bit3=dot4, bit4=dot5, bit5=dot6
    let mask = 0;
    for (const d of activeDots) {
      mask |= 1 << (d - 1);
    }
    return String.fromCodePoint(0x2800 + mask);
  }

  function getActiveDots() {
    return el.dots
      .filter((btn) => btn.getAttribute("aria-pressed") === "true")
      .map((btn) => Number(btn.dataset.dot));
  }

  function clearDots() {
    el.dots.forEach((btn) => btn.setAttribute("aria-pressed", "false"));
  }

  function speak(text) {
    if (!window.speechSynthesis || !text) return;
    window.speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = "vi-VN";
    utter.rate = 0.95;
    const voices = window.speechSynthesis.getVoices();
    const vi = voices.find((v) => v.lang.toLowerCase().startsWith("vi"));
    if (vi) utter.voice = vi;
    window.speechSynthesis.speak(utter);
    setStatus("Đang đọc…");
    utter.onend = () => setStatus("Đã đọc xong");
  }

  function ensureAudio() {
    if (!audioCtx) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (audioCtx.state === "suspended") audioCtx.resume();
    return audioCtx;
  }

  function tone(durationMs, frequency = 680) {
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
    gain.gain.exponentialRampToValueAtTime(0.2, now + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + durationMs / 1000);
    osc.start(now);
    osc.stop(now + durationMs / 1000 + 0.02);
    return durationMs + 40;
  }

  async function playMorseAudio(code) {
    const unit = 90;
    setStatus("Đang phát Morse…");
    for (const ch of code) {
      if (ch === ".") {
        tone(unit);
        await wait(unit * 2);
      } else if (ch === "-") {
        tone(unit * 3);
        await wait(unit * 4);
      } else if (ch === " ") {
        await wait(unit * 3);
      } else if (ch === "/") {
        await wait(unit * 5);
      }
    }
    setStatus("Đã phát xong");
  }

  function wait(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function switchMode(mode) {
    el.navBtns.forEach((btn) => {
      const active = btn.dataset.mode === mode;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-pressed", String(active));
    });
    el.panels.forEach((panel) => {
      const match = panel.dataset.panel === mode;
      panel.hidden = !match;
      panel.classList.toggle("is-visible", match);
    });
  }

  function appendMorse(token) {
    const cur = el.morseInput.value;
    if (!cur || /[\s/]$/.test(cur)) {
      el.morseInput.value = cur + token;
    } else {
      el.morseInput.value = cur + token;
    }
    el.morseInput.focus();
  }

  // --- Events ---
  el.navBtns.forEach((btn) => {
    btn.addEventListener("click", () => switchMode(btn.dataset.mode));
  });

  document.getElementById("btn-decode-morse").addEventListener("click", () => {
    const decoded = decodeMorse(el.morseInput.value);
    setResult(decoded || "", decoded ? "Morse → chữ" : "Không có dữ liệu");
  });

  document.getElementById("btn-encode-morse").addEventListener("click", () => {
    const encoded = encodeMorse(el.textToMorse.value);
    el.morseInput.value = encoded;
    lastMorseCode = encoded;
    el.btnPlayMorse.disabled = !encoded;
    setResult(encoded, "Chữ → Morse");
  });

  document.getElementById("btn-clear-morse").addEventListener("click", () => {
    el.morseInput.value = "";
    el.textToMorse.value = "";
    el.btnPlayMorse.disabled = true;
    setResult("Kết quả giải mã sẽ hiện ở đây.", "Sẵn sàng");
    el.btnSpeak.disabled = true;
    el.btnCopy.disabled = true;
  });

  document.getElementById("tap-dot").addEventListener("click", () => appendMorse("."));
  document.getElementById("tap-dash").addEventListener("click", () => appendMorse("-"));
  document.getElementById("tap-letter").addEventListener("click", () => {
    const v = el.morseInput.value;
    if (v && !/\s$/.test(v)) el.morseInput.value = v + " ";
  });
  document.getElementById("tap-word").addEventListener("click", () => {
    const v = el.morseInput.value.trimEnd();
    el.morseInput.value = v ? `${v} / ` : "";
  });
  document.getElementById("tap-back").addEventListener("click", () => {
    el.morseInput.value = el.morseInput.value.slice(0, -1);
  });

  document.getElementById("btn-play-morse").addEventListener("click", () => {
    const code = el.morseInput.value.trim() || lastMorseCode;
    if (code) playMorseAudio(code);
  });

  document.getElementById("btn-decode-braille").addEventListener("click", () => {
    const decoded = decodeBraille(el.brailleInput.value);
    setResult(decoded || "", decoded ? "Braille → chữ" : "Không có dữ liệu");
  });

  document.getElementById("btn-encode-braille").addEventListener("click", () => {
    const encoded = encodeBraille(el.textToBraille.value);
    el.brailleInput.value = encoded;
    setResult(encoded, "Chữ → Braille");
  });

  document.getElementById("btn-clear-braille").addEventListener("click", () => {
    el.brailleInput.value = "";
    el.textToBraille.value = "";
    clearDots();
    setResult("Kết quả giải mã sẽ hiện ở đây.", "Sẵn sàng");
    el.btnSpeak.disabled = true;
    el.btnCopy.disabled = true;
  });

  el.dots.forEach((btn) => {
    btn.addEventListener("click", () => {
      const pressed = btn.getAttribute("aria-pressed") === "true";
      btn.setAttribute("aria-pressed", String(!pressed));
    });
  });

  document.getElementById("btn-commit-braille").addEventListener("click", () => {
    const cell = dotsToBrailleChar(getActiveDots());
    el.brailleInput.value += cell;
    clearDots();
  });

  document.getElementById("btn-space-braille").addEventListener("click", () => {
    el.brailleInput.value += "⠀";
  });

  document.getElementById("btn-back-braille").addEventListener("click", () => {
    const chars = [...el.brailleInput.value];
    chars.pop();
    el.brailleInput.value = chars.join("");
  });

  PHRASES.forEach((phrase) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "phrase-btn";
    btn.setAttribute("role", "listitem");
    btn.textContent = phrase;
    btn.addEventListener("click", () => {
      setResult(phrase, "Câu nhanh");
      speak(phrase);
    });
    el.phraseBoard.appendChild(btn);
  });

  el.btnSpeak.addEventListener("click", () => {
    const text = el.resultText.textContent;
    if (text && text !== "(trống)" && text !== "Kết quả giải mã sẽ hiện ở đây.") {
      speak(text);
    }
  });

  el.btnStop.addEventListener("click", () => {
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    setStatus("Đã dừng");
  });

  el.btnCopy.addEventListener("click", async () => {
    const text = el.resultText.textContent;
    try {
      await navigator.clipboard.writeText(text);
      setStatus("Đã sao chép");
    } catch {
      setStatus("Không sao chép được");
    }
  });

  document.getElementById("btn-demo-speak").addEventListener("click", () => {
    speak(
      "Xin chào. Đây là Mã Mở, hệ thống giải mã Morse và Braille hỗ trợ người khiếm khuyết giao tiếp."
    );
  });

  // Prefill helpful demos
  el.morseInput.value = ".... . .-.. .-.. --- / .-- --- .-. .-.. -..";
  el.textToBraille.value = "xin chao";

  if (window.speechSynthesis) {
    window.speechSynthesis.getVoices();
    window.speechSynthesis.onvoiceschanged = () => window.speechSynthesis.getVoices();
  }
})();
