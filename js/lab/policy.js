/**
 * policy — biên giới môi trường Lab (phòng thủ)
 * Lab tách khỏi a11y / crypto UI; không thực thi mẫu người dùng.
 */
(function (global) {
  "use strict";

  const Policy = {
    id: "lab",
    version: "1.0.0",
    surface: "isolated",
    mode: "defensive-only",

    /** Domains độc quyền — không chồng a11y/crypto recommend */
    owns: [
      "malware-static",
      "security-audit",
      "sandbox-policy",
      "ioc-triage",
    ],

    rules: {
      neverExecuteSample: true,
      neverEvalInput: true,
      neverFetchSampleUrl: true,
      neverPersistSampleByDefault: true,
      workerOnlyAnalysis: true,
      noMainThreadSideEffects: true,
      educationalHeuristicsOnly: true,
      noExploitGeneration: true,
      noAttackPayloads: true,
    },

    /** Cho phép / cấm trên UI Lab */
    allow: [
      "static-text-scan",
      "format-classify-readonly",
      "entropy-metrics",
      "pattern-heuristics",
      "self-hardening-checklist",
      "export-json-report",
    ],

    deny: [
      "eval",
      "Function-constructor",
      "dynamic-import-of-sample",
      "iframe-srcdoc-execution",
      "blob-url-script-run",
      "network-exfil-of-sample",
      "write-sample-to-disk-without-quarantine",
    ],

    quarantine: {
      memoryOnly: true,
      maxChars: 200_000,
      wipeOnLeave: true,
    },

    assertSafe(action) {
      if (Policy.deny.includes(action)) {
        return { ok: false, reason: `Bị cấm bởi Lab policy: ${action}` };
      }
      if (!Policy.allow.includes(action) && !action.startsWith("ui.")) {
        return { ok: false, reason: `Hành động không nằm allow-list: ${action}` };
      }
      return { ok: true };
    },

    describe() {
      return {
        id: Policy.id,
        version: Policy.version,
        mode: Policy.mode,
        owns: Policy.owns.slice(),
        rules: { ...Policy.rules },
        allow: Policy.allow.slice(),
        deny: Policy.deny.slice(),
        quarantine: { ...Policy.quarantine },
        note: "Môi trường tách biệt — chỉ phân tích tĩnh / kiểm thử phòng thủ. Không chạy mã mẫu.",
      };
    },
  };

  global.MaMoLabModules = global.MaMoLabModules || {};
  global.MaMoLabModules.policy = Policy;
})(typeof window !== "undefined" ? window : globalThis);
