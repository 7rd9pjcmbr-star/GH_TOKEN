/**
 * Phrases — bảng câu giao tiếp / khẩn cấp.
 */
(function (global) {
  "use strict";

  const CATALOG = {
    emergency: [
      { id: "help", text: "Tôi cần giúp đỡ", priority: 1 },
      { id: "pain", text: "Tôi đau", priority: 1 },
      { id: "call", text: "Gọi người thân", priority: 1 },
    ],
    needs: [
      { id: "thirst", text: "Tôi khát", priority: 2 },
      { id: "hunger", text: "Tôi đói", priority: 2 },
      { id: "tired", text: "Tôi mệt", priority: 2 },
      { id: "lie", text: "Tôi muốn nằm", priority: 3 },
      { id: "sit", text: "Tôi muốn ngồi", priority: 3 },
    ],
    social: [
      { id: "yes", text: "Đồng ý", priority: 2 },
      { id: "no", text: "Không", priority: 2 },
      { id: "wait", text: "Xin chờ một chút", priority: 3 },
      { id: "hi", text: "Xin chào", priority: 3 },
      { id: "thanks", text: "Cảm ơn", priority: 3 },
      { id: "ok", text: "Tôi ổn", priority: 3 },
      { id: "sorry", text: "Xin lỗi", priority: 3 },
    ],
    environment: [
      { id: "window", text: "Mở cửa sổ", priority: 4 },
      { id: "light", text: "Tắt đèn", priority: 4 },
      { id: "fan", text: "Bật quạt", priority: 4 },
    ],
  };

  function flat() {
    return Object.values(CATALOG).flat().sort((a, b) => a.priority - b.priority);
  }

  function setResult(text, status) {
    const result = document.getElementById("result-text");
    const pill = document.getElementById("status-pill");
    const speakBtn = document.getElementById("btn-speak");
    const copyBtn = document.getElementById("btn-copy");
    if (result) result.textContent = text;
    if (pill) pill.textContent = status || "Đã chọn";
    const usable = text && text !== "Chưa có câu nào được chọn." && text !== "(trống)";
    if (speakBtn) speakBtn.disabled = !usable;
    if (copyBtn) copyBtn.disabled = !usable;
  }

  const Phrases = {
    catalog: CATALOG,
    all: flat,

    say(text) {
      setResult(text, "Đã nói");
      global.MaMoA11y.core.get("speech")?.announce(text);
      global.MaMoA11y.core.get("speech")?.speak(text);
      global.MaMoA11y.core.emit("phrase:said", { text });
    },

    renderScanBoard(container) {
      if (!container) return;
      container.innerHTML = "";
      flat().forEach((item) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "scan-target phrase-scan";
        btn.setAttribute("role", "listitem");
        btn.dataset.scanLabel = item.text;
        btn.dataset.phrase = item.text;
        btn.dataset.phraseId = item.id;
        btn.textContent = item.text;
        btn.addEventListener("click", () => Phrases.say(item.text));
        container.appendChild(btn);
      });
    },

    renderPhraseBoard(container) {
      if (!container) return;
      container.innerHTML = "";
      flat().forEach((item) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "phrase-btn";
        btn.setAttribute("role", "listitem");
        btn.textContent = item.text;
        btn.addEventListener("click", () => Phrases.say(item.text));
        container.appendChild(btn);
      });
    },

    start() {
      Phrases.renderScanBoard(document.getElementById("scan-board"));
      Phrases.renderPhraseBoard(document.getElementById("phrase-board"));

      document.querySelectorAll(".yesno[data-phrase]").forEach((btn) => {
        btn.addEventListener("click", () => Phrases.say(btn.dataset.phrase));
      });
    },
  };

  global.MaMoA11y.core.register("phrases", Phrases);
  // compat helpers used by result bar
  global.MaMoA11y.setResult = setResult;
})(window);
