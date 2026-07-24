# 08 — Kiểm thử bề mặt web

## Chủ đề học (từ catalog http)

- RCE class lịch sử: deserialization, template injection, upload
- Authn/z bypass patterns
- Disclosure (version, path, debug)
- Dependency CVE (Log4Shell, Rails YAML, …)

## Cách thí nghiệm an toàn

1. App **owned** / staging
2. Checklist cấu hình + dependency scan (OWASP Dependency-Check, npm audit, …)
3. Không chạy module exploit MSF

## MaMoLab

Dán response/header/log text vào `/lab/` → static heuristics.

EXP-04 bổ sung phần HTTP.
