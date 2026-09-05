(() => {
  "use strict";

  const input = document.getElementById("lab-input");
  const out = document.getElementById("lab-output");
  const status = document.getElementById("lab-status");
  const riskBox = document.getElementById("lab-risk");
  const riskBand = document.getElementById("lab-risk-band");
  const riskMeta = document.getElementById("lab-risk-meta");

  function setStatus(msg) {
    if (status) status.textContent = msg;
  }

  function showRisk(report) {
    const band = report?.summary?.riskBand || report?.static?.risk?.band;
    if (!band || !riskBox) return;
    riskBox.hidden = false;
    riskBand.textContent = band;
    riskBand.className = `lab-risk-band is-${band}`;
    riskMeta.textContent = `score ${report.summary?.riskScore ?? "—"} · findings ${
      report.summary?.findingCount ?? 0
    } · executed=${report.isolation?.executedSample === false ? "no" : "?"}`;
  }

  function renderBoundaries() {
    const box = document.getElementById("lab-boundaries");
    if (!box || !window.MaMoLab) return;
    const d = window.MaMoLab.describe();
    const p = d.isolation || {};
    box.innerHTML = `
      <div class="lab-bound">
        <h3>Allow</h3>
        <ul>${(p.allow || []).map((x) => `<li>${x}</li>`).join("")}</ul>
      </div>
      <div class="lab-bound">
        <h3>Deny</h3>
        <ul>${(p.deny || []).map((x) => `<li>${x}</li>`).join("")}</ul>
      </div>
      <div class="lab-bound">
        <h3>Owns</h3>
        <ul>${(p.owns || []).map((x) => `<li>${x}</li>`).join("")}</ul>
      </div>
      <div class="lab-bound">
        <h3>Guarantees</h3>
        <ul>${(d.guarantees || []).map((x) => `<li>${x}</li>`).join("")}</ul>
      </div>
    `;
  }

  async function analyze() {
    if (!window.MaMoLab?.analyze) {
      out.textContent = JSON.stringify({ error: "MaMoLab missing" }, null, 2);
      return;
    }
    setStatus("Đang phân tích trong sandbox…");
    const text = input.value || "";
    try {
      const report = await window.MaMoLab.analyze(text, {
        includeHarden: false,
        forceLocal: false,
      });
      // Nếu Worker chưa gắn format, bổ sung format từ MaMoLogic trên main (readonly)
      if (!report.format && window.MaMoLogicModules?.analyze) {
        report.format = window.MaMoLogicModules.analyze.analyze(text, { limit: 5 });
        if (report.summary) {
          report.summary.formatPrimary = report.format?.primary?.id || null;
        }
      } else if (!report.format && window.MaMoLabModules?.static && window.MaMoLogic?.analyze) {
        report.format = window.MaMoLogic.analyze(text, { limit: 5 });
      }
      // Rebuild summary-friendly view
      const view = {
        summary: report.summary,
        isolation: report.isolation,
        topFindings: report.topFindings || report.static?.findings?.slice(0, 12),
        indicators: report.indicators?.counts || report.indicators,
        format: report.format?.primary
          ? {
              id: report.format.primary.id,
              label: report.format.primary.label,
              confidence: report.format.primary.confidence,
              uniqueness: report.format.primary.uniqueness,
            }
          : null,
        disclaimer: report.disclaimer,
      };
      out.textContent = JSON.stringify(view, null, 2);
      showRisk(report);
      setStatus(
        `Xong · worker=${window.MaMoLabModules.sandbox.isWorkerReady() ? "yes" : "fallback"} · không thực thi mẫu`
      );
    } catch (err) {
      out.textContent = JSON.stringify({ error: String(err.message || err) }, null, 2);
      setStatus("Lỗi phân tích");
    }
  }

  function audit() {
    if (!window.MaMoLab?.audit) return;
    const a = window.MaMoLab.audit();
    out.textContent = JSON.stringify(a, null, 2);
    riskBox.hidden = false;
    riskBand.textContent = a.ok ? "hardened" : "gaps";
    riskBand.className = `lab-risk-band ${a.ok ? "is-low" : "is-high"}`;
    riskMeta.textContent = `passed ${a.passed}/${a.passed + a.failed}`;
    setStatus("Kiểm thử bảo mật (self-audit) hoàn tất");
  }

  function wipe() {
    if (input) input.value = "";
    window.MaMoLab?.wipe?.();
    out.textContent = "{}";
    riskBox.hidden = true;
    setStatus("Đã xóa mẫu khỏi UI + terminate Worker");
  }

  document.getElementById("btn-lab-analyze")?.addEventListener("click", () => {
    analyze();
  });
  document.getElementById("btn-lab-audit")?.addEventListener("click", audit);
  document.getElementById("btn-lab-wipe")?.addEventListener("click", wipe);

  // Không giữ mẫu khi rời trang
  window.addEventListener("pagehide", () => {
    if (input) input.value = "";
    window.MaMoLab?.wipe?.();
  });

  renderBoundaries();
  setStatus(
    `Sẵn sàng · ${window.MaMoLab?.describe?.().mode || "loading"} · CSP connect-src none`
  );
})();
