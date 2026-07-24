# 01 — Môi trường thí nghiệm

## Ba lớp cô lập

1. **Host UI `/lab/`** — CSP chặt, Worker static-only
2. **MaMoLab `js/lab/*`** — policy · static · indicators · harden · report
3. **Docker `docker/lab`** — `network_mode: none`, RO quarantine

## Thao tác chuẩn

```bash
# Browser
# mở /lab/ → dán text → Analyze → Audit

# Docker
mkdir -p quarantine reports
# copy mẫu owned vào quarantine/
docker compose -f docker/lab/docker-compose.yml run --rm lab analyze /quarantine/<file>
```

## Kiểm chứng môi trường

- [ ] `MaMoLab.audit()` pass
- [ ] Docker lab không có network
- [ ] Không cài msfvenom trong image lab

## Liên kết

- `docs/SECURITY-LAB.md`
- `docker/lab/README.md`
- EXP-01, EXP-02, EXP-07
