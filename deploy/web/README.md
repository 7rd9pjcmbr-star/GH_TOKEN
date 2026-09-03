# Mã Mở — production web tier

Serves the static UI (`/`, `/atlas/`, `/mapper/`, `/logic-view/`, `/lab/`) with a
hardened nginx: gzip, asset caching, a health endpoint, and security headers
(CSP, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
`Permissions-Policy`, `Cross-Origin-Opener-Policy`).

This replaces the development `python3 -m http.server 8080` with a real web server.

## Files

- `nginx.conf` — production nginx configuration (main + `http` + `server`).
- `Dockerfile` — builds `mamo-web` from `nginx:1.27-alpine`, copying only the
  front-end surfaces (never `scripts/`, `secrets/`, `reports/`, …).
- `docker-compose.yml` — runs the web tier, publishing `8080 -> 80`.

## Run with Docker (recommended)

```bash
docker compose -f deploy/web/docker-compose.yml up -d --build
curl -fsS http://localhost:8080/healthz        # -> ok
open http://localhost:8080/
```

## Run with nginx directly (no Docker)

```bash
sudo nginx -c "$PWD/deploy/web/nginx.conf" -p "$PWD"     # after adjusting root
sudo nginx -s reload                                     # apply config changes
sudo nginx -s stop                                       # stop
```

When running directly, point `root` at the repository directory instead of
`/usr/share/nginx/html` (the container path).

## Config validation

```bash
docker run --rm -v "$PWD/deploy/web/nginx.conf":/etc/nginx/nginx.conf:ro nginx:1.27-alpine nginx -t
```

## Notes

- `/healthz` returns `200 ok` for load balancers / orchestrator liveness+readiness probes.
- TLS termination is expected at the edge (ingress / load balancer / CDN). Add a
  `listen 443 ssl;` server and certificates when terminating TLS at nginx itself.
- The Python backends (`scripts/`, Pancake POS / GHN / Telegram) are **not** part of
  this image; they require live credentials and run as separate services.
