# 00 — Chính sách, đạo đức & phạm vi lab

## Học gì

Phân biệt **học kiến thức tấn công công khai** (CVE, kỹ thuật đã công bố) với **hành vi tấn công**.  
Thư viện này chỉ phục vụ lớp thứ nhất + phòng thủ.

## Quy tắc cứng

1. Chỉ thí nghiệm trên hệ **owned** hoặc lab cô lập.
2. Không generate payload, không chạy exploit framework trên target thật.
3. Không dùng dump stealer / Acc_all để đăng nhập.
4. Không paste mẫu độc vào chat công khai.
5. Mọi “module Metasploit” trong tài liệu = **tham chiếu catalog**, không phải lệnh chạy.

## Ánh xạ policy code

- `js/lab/policy.js` → `noExploitGeneration`, `neverExecuteSample`
- `docs/SECURITY-LAB.md`
- Deny: msfvenom · msfrpcd · detonate

## Bài tập tư duy

Viết 5 dòng: “Nếu tôi thấy module `exploits/windows/...` trong catalog, việc đúng để làm tiếp theo là gì?”  
(Đáp án mong đợi: tra CVE → kiểm tra patch owned → rule detect — không `exploit`.)
