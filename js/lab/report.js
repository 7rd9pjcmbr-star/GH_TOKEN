/**
 * report — tổng hợp báo cáo Lab (static + IOC + format + harden)
 */
(function (global) {
  "use strict";

  function severityRank(s) {
    return { critical: 4, high: 3, medium: 2, low: 1 }[s] || 0;
  }

  const Report = {
    build(parts = {}) {
      const policy = global.MaMoLabModules?.policy?.describe?.() || null;
      const staticReport = parts.static || null;
      const iocs = parts.indicators || null;
      const format = parts.format || null;
      const harden = parts.harden || null;

      const findings = (staticReport?.findings || []).slice().sort(
        (a, b) => severityRank(b.severity) - severityRank(a.severity)
      );

      return {
        ok: true,
        generatedAt: new Date().toISOString(),
        isolation: {
          surface: "lab",
          executedSample: false,
          networkUsed: false,
          policy,
        },
        summary: {
          riskBand: staticReport?.risk?.band || "unknown",
          riskScore: staticReport?.risk?.score ?? null,
          findingCount: findings.length,
          iocCount: iocs?.counts || null,
          formatPrimary: format?.primary?.id || format?.id || null,
          hardenOk: harden ? !!harden.ok : null,
        },
        static: staticReport,
        indicators: iocs,
        format,
        harden,
        topFindings: findings.slice(0, 12),
        disclaimer:
          "Báo cáo giáo dục/phòng thủ. Không chứng minh mã độc; không thay thế sandbox chuyên nghiệp.",
      };
    },
  };

  global.MaMoLabModules = global.MaMoLabModules || {};
  global.MaMoLabModules.report = Report;
})(typeof window !== "undefined" ? window : globalThis);
