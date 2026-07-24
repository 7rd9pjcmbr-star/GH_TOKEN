# 02 — Mô hình đe dọa phía phòng thủ

## Tài sản (assets)

- Endpoint (Windows/Linux)
- Dịch vụ mạng (HTTP, SSH, SMB, RDP, DB)
- Ứng dụng web / API
- Credential & session
- Pipeline CI / secrets

## Kẻ thù (từ góc catalog MSF)

| Class MSF | Ý nghĩa với defender |
|-----------|----------------------|
| exploit | CVE đã có kỹ thuật công khai → vá/detect |
| auxiliary/scanner | Bề mặt bị dò → harden |
| post | Sau xâm nhập → IOC persistence/lateral |
| payload/encoder | Artifact → signature/YARA |
| evasion | Kỹ thuật né AV → nâng detection |

## Câu hỏi threat model mỗi hệ owned

1. Attack surface nào expose?
2. CVE nào trong harvest khớp stack?
3. Log/detect chỗ nào bắt được post-TTP?
4. Blast radius nếu credential lộ?

## Bài tập

Chọn 1 hệ owned → điền bảng trên (1 trang).
