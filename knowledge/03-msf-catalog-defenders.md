# 03 — Metasploit như thư viện tham chiếu (defenders)

Metasploit Framework ≈ **bách khoa kỹ thuật đã công bố**.  
Dùng để học *cái gì tồn tại trên thế giới*, không để *bắn* vào hệ thống.

## Vì sao cần catalog này khi học?

1. CVE kèm module path → biết kỹ thuật đã công khai.
2. Cây thư mục (windows/http/smb…) → bản đồ bề mặt tấn công lịch sử.
3. Rank → tín hiệu “ổn định trong catalog” → ưu tiên vá/học trước.
4. `def check` (~60% exploits) → khái niệm *verify without exploit* (chỉ học ý tưởng, không chạy).

## Công cụ trong repo

```bash
python3 scripts/metasploit_suite_mapper.py      # taxonomy suite
python3 scripts/metasploit_library_harvest.py   # metadata ~5k modules
python3 scripts/metasploit_testing_knowledge.py # playbook P1–P6
python3 scripts/knowledge_library_build.py --with-harvest
```

Đọc sau harvest:

- `knowledge/generated/msf-summary.md`
- `knowledge/generated/cve-top.md`
- `knowledge/generated/study-cards.json`
- `reports/telegram-classify/metasploit_cve_index.csv`

## Cách đọc một module path

Ví dụ: `exploits/windows/http/something_cve_2021_....rb`

| Phần | Ý nghĩa học |
|------|-------------|
| exploits | Class = CVE/kỹ thuật RCE hoặc privilege |
| windows | Platform owned cần quan tâm |
| http | Vector dịch vụ |
| cve_2021_… | Chỉ mục CVE → patch/detect |

**Hành động đúng:** tra CVE → inventory owned → patch → rule detect.  
**Hành động sai:** `msfconsole` → `use` → `exploit` trên hệ không phải lab owned.

## Rank → ưu tiên học/vá

| Rank | Việc nên làm |
|------|----------------|
| excellent / great | Học kỹ + đưa vào P1 vá nếu stack khớp |
| good / normal | Backlog + detect |
| average / low | Theo dõi |
| manual | Đọc điều kiện; không coi là “auto critical” |

## Bài tập

1. Mở `generated/msf-summary.md` — ghi 5 subtree exploit lớn nhất.
2. Với 1 subtree (vd `http`) liệt kê 3 rủi ro cấu hình tương ứng (không cần tên module).
3. Trả lời: “Catalog thay thế được scanner nội bộ không?” (Đáp án: không — chỉ là chỉ mục kiến thức).
