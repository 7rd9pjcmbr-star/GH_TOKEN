/**
 * sandbox — cầu nối UI ↔ Web Worker (cô lập phân tích)
 * Mẫu chỉ gửi dạng text; Worker không được phép fetch / eval.
 */
(function (global) {
  "use strict";

  let worker = null;
  let seq = 0;
  const pending = new Map();

  function workerUrl() {
    try {
      return new URL("../lab/worker/sandbox.worker.js", global.location.href).href;
    } catch {
      return "worker/sandbox.worker.js";
    }
  }

  function ensureWorker() {
    if (worker) return worker;
    if (typeof Worker === "undefined") return null;
    try {
      worker = new Worker(workerUrl());
      worker.onmessage = (ev) => {
        const msg = ev.data || {};
        const p = pending.get(msg.id);
        if (!p) return;
        pending.delete(msg.id);
        if (msg.error) p.reject(new Error(msg.error));
        else p.resolve(msg.result);
      };
      worker.onerror = () => {
        /* fallback handled per-call */
      };
      return worker;
    } catch {
      worker = null;
      return null;
    }
  }

  function analyzeLocal(text, opts) {
    const M = global.MaMoLabModules;
    const staticReport = M.static.analyze(text, opts);
    const indicators = M.indicators.extract(text, opts);
    let format = null;
    if (global.MaMoLogic?.analyze) {
      try {
        format = global.MaMoLogic.analyze(text, { limit: 5 });
      } catch {
        format = null;
      }
    }
    const harden = opts?.includeHarden ? M.harden.audit() : null;
    return M.report.build({ static: staticReport, indicators, format, harden });
  }

  const Sandbox = {
    isWorkerReady() {
      return !!ensureWorker();
    },

    /** Phân tích sâu trong Worker nếu được; fallback local static */
    async analyze(text, opts = {}) {
      const policy = global.MaMoLabModules.policy;
      const gate = policy.assertSafe("static-text-scan");
      if (!gate.ok) {
        return { ok: false, error: gate.reason };
      }
      const max = policy.quarantine.maxChars;
      const sample = String(text ?? "").slice(0, max);

      const w = ensureWorker();
      if (w && opts.forceLocal !== true) {
        const id = ++seq;
        return new Promise((resolve, reject) => {
          const timer = setTimeout(() => {
            pending.delete(id);
            resolve(analyzeLocal(sample, opts));
          }, opts.timeoutMs || 8000);
          pending.set(id, {
            resolve: (r) => {
              clearTimeout(timer);
              resolve(r);
            },
            reject: (e) => {
              clearTimeout(timer);
              reject(e);
            },
          });
          w.postMessage({
            id,
            type: "analyze",
            text: sample,
            opts: {
              maxChars: max,
              includeHarden: !!opts.includeHarden,
            },
          });
        }).catch(() => analyzeLocal(sample, opts));
      }
      return analyzeLocal(sample, opts);
    },

    wipe() {
      if (worker) {
        try {
          worker.terminate();
        } catch {
          /* ignore */
        }
        worker = null;
      }
      pending.clear();
      return { ok: true, wiped: true };
    },
  };

  global.MaMoLabModules = global.MaMoLabModules || {};
  global.MaMoLabModules.sandbox = Sandbox;
})(typeof window !== "undefined" ? window : globalThis);
