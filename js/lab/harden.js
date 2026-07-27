/**
 * harden — kiểm thử bảo mật / tự cứng hóa surface Mã Mở (phòng thủ)
 * Không tấn hệ thống ngoài; chỉ checklist + quét mã nguồn đã nạp.
 */
(function (global) {
  "use strict";

  const CHECKS = [
    {
      id: "csp-meta",
      title: "Content-Security-Policy (khuyến nghị)",
      level: "high",
      run() {
        const meta = typeof document !== "undefined"
          ? document.querySelector('meta[http-equiv="Content-Security-Policy"]')
          : null;
        return {
          pass: !!meta,
          detail: meta
            ? "Có meta CSP trên trang hiện tại"
            : "Chưa thấy CSP meta — nên thêm CSP chặt (default-src 'self').",
        };
      },
    },
    {
      id: "lab-policy-loaded",
      title: "Lab policy đã nạp",
      level: "critical",
      run() {
        const p = global.MaMoLabModules?.policy;
        return {
          pass: !!(p && p.rules?.neverExecuteSample),
          detail: p ? "Policy phòng thủ active" : "Thiếu MaMoLabModules.policy",
        };
      },
    },
    {
      id: "no-inline-eval-helpers",
      title: "Không export eval helper",
      level: "critical",
      run() {
        const bad =
          typeof global.MaMoLab?.evalSample === "function" ||
          typeof global.MaMoLogic?.evalSample === "function";
        return {
          pass: !bad,
          detail: bad ? "Phát hiện API nguy hiểm evalSample" : "Không có API thực thi mẫu",
        };
      },
    },
    {
      id: "worker-isolation",
      title: "Phân tích qua Worker (khuyến nghị)",
      level: "medium",
      run() {
        const ok = typeof Worker !== "undefined";
        return {
          pass: ok,
          detail: ok
            ? "Web Worker khả dụng — dùng lab/worker/sandbox.worker.js"
            : "Worker không khả dụng; fallback main-thread static only",
        };
      },
    },
    {
      id: "logic-ownership-clean",
      title: "Ownership logic không xung đột",
      level: "medium",
      run() {
        const c = global.MaMoLogic?.config?.conflicts?.();
        if (!c) {
          return { pass: true, detail: "MaMoLogic chưa nạp (OK nếu chỉ mở Lab)" };
        }
        return {
          pass: !!c.ok,
          detail: c.ok ? "Không domain trùng" : JSON.stringify(c.conflicts || []),
        };
      },
    },
    {
      id: "sensitive-storage",
      title: "Không lưu mẫu Lab mặc định",
      level: "high",
      run() {
        let leaked = false;
        try {
          for (let i = 0; i < localStorage.length; i++) {
            const k = localStorage.key(i) || "";
            if (/mamo\.lab\.sample/i.test(k)) leaked = true;
          }
        } catch {
          /* private */
        }
        return {
          pass: !leaked,
          detail: leaked
            ? "Có key lưu mẫu trong localStorage — nên xóa"
            : "Không thấy sample persistence key",
        };
      },
    },
  ];

  const Harden = {
    checklist() {
      return CHECKS.map((c) => ({ id: c.id, title: c.title, level: c.level }));
    },

    audit() {
      const results = CHECKS.map((c) => {
        let out;
        try {
          out = c.run();
        } catch (err) {
          out = { pass: false, detail: String(err.message || err) };
        }
        return {
          id: c.id,
          title: c.title,
          level: c.level,
          pass: !!out.pass,
          detail: out.detail,
        };
      });
      const failed = results.filter((r) => !r.pass);
      return {
        ok: failed.length === 0,
        passed: results.filter((r) => r.pass).length,
        failed: failed.length,
        results,
        recommendations: [
          "Chạy phân tích mẫu trong Docker Lab (network: none).",
          "Không paste mẫu vào chat/log công khai.",
          "Thêm CSP + COOP/COEP nếu host production.",
          "Giữ Lab tách route /lab/ khỏi surface a11y.",
        ],
      };
    },
  };

  global.MaMoLabModules = global.MaMoLabModules || {};
  global.MaMoLabModules.harden = Harden;
})(typeof window !== "undefined" ? window : globalThis);
