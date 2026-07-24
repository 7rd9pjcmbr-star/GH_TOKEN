#!/usr/bin/env bash
# entrypoint — chỉ thao tác trong /quarantine + /reports; không mạng.
set -euo pipefail

cmd="${1:-help}"
shift || true

case "$cmd" in
  help|--help|-h)
    cat <<'EOF'
MaMoLab Docker (network: none)

  analyze <file>   Phân tích tĩnh file trong /quarantine
  list             Liệt kê mẫu trong /quarantine
  wipe             Xóa nội dung /quarantine (giữ thư mục)
  shell            Bash tương tác (vẫn không mạng nếu compose đúng)

Mẫu mount read-only vào /quarantine. Không copy ra host trừ /reports.
EOF
    ;;
  list)
    ls -la /quarantine
    ;;
  wipe)
    find /quarantine -mindepth 1 -maxdepth 1 -exec rm -rf {} +
    echo "quarantine wiped"
    ;;
  analyze)
    file="${1:-}"
    if [[ -z "$file" ]]; then
      echo "usage: analyze <path-under-/quarantine>" >&2
      exit 2
    fi
    # Chỉ cho phép đường dẫn trong /quarantine
    real="$(realpath -m "$file")"
    case "$real" in
      /quarantine/*) ;;
      *) echo "refusing path outside /quarantine: $real" >&2; exit 3 ;;
    esac
    if [[ ! -f "$real" ]]; then
      echo "not a file: $real" >&2
      exit 4
    fi
    python3 /lab/analyze-static.py "$real"
    ;;
  shell)
    exec /bin/bash
    ;;
  *)
    echo "unknown command: $cmd" >&2
    exit 1
    ;;
esac
