# 11 — API MaMoLab & tự kiểm thử

```js
await MaMoLab.analyze(text)
MaMoLab.audit()
MaMoLab.wipe()
MaMoLab.describe()
```

## Owns

`malware-static` · `security-audit` · `sandbox-policy` · `ioc-triage`

## Self-test checklist (harden.js)

CSP · policy loaded · no eval helper · Worker · ownership · storage leak

EXP-01 bắt buộc trước mọi thí nghiệm khác.
