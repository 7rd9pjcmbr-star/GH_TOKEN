/**
 * indicators — trích xuất IOC phòng thủ từ văn bản (không gọi mạng)
 */
(function (global) {
  "use strict";

  function uniq(arr) {
    return [...new Set(arr)];
  }

  const Indicators = {
    extract(input, opts = {}) {
      const max = opts.maxChars || 200_000;
      const text = String(input ?? "").slice(0, max);

      const ipv4 =
        text.match(/\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b/g) ||
        [];
      const urls = text.match(/https?:\/\/[^\s"'<>]+/gi) || [];
      const emails = text.match(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g) || [];
      const md5 = text.match(/\b[a-fA-F0-9]{32}\b/g) || [];
      const sha1 = text.match(/\b[a-fA-F0-9]{40}\b/g) || [];
      const sha256 = text.match(/\b[a-fA-F0-9]{64}\b/g) || [];
      const domains =
        text.match(/\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,24})\b/gi) || [];

      const cleanDomains = uniq(
        domains
          .map((d) => d.toLowerCase())
          .filter((d) => !/^(?:\d+\.)+\d+$/.test(d))
          .filter((d) => !d.endsWith(".js") && !d.endsWith(".css") && !d.endsWith(".json"))
      ).slice(0, 40);

      return {
        ok: true,
        networkCalls: false,
        iocs: {
          ipv4: uniq(ipv4).slice(0, 40),
          urls: uniq(urls).slice(0, 40),
          emails: uniq(emails).slice(0, 20),
          domains: cleanDomains,
          hashes: {
            md5: uniq(md5).slice(0, 20),
            sha1: uniq(sha1).slice(0, 20),
            sha256: uniq(sha256).slice(0, 20),
          },
        },
        counts: {
          ipv4: uniq(ipv4).length,
          urls: uniq(urls).length,
          emails: uniq(emails).length,
          domains: cleanDomains.length,
          hashes:
            uniq(md5).length + uniq(sha1).length + uniq(sha256).length,
        },
        note: "IOC chỉ trích từ văn bản — Lab không resolve DNS / không fetch URL.",
      };
    },
  };

  global.MaMoLabModules = global.MaMoLabModules || {};
  global.MaMoLabModules.indicators = Indicators;
})(typeof window !== "undefined" ? window : globalThis);
