#!/usr/bin/env python3
"""Alias cho auto_backup_credential — đọc/backup credential, cookie, session token.

Dùng:
  PYTHONPATH=scripts python3 scripts/backup_credential.py status
  PYTHONPATH=scripts python3 scripts/backup_credential.py bootstrap
  PYTHONPATH=scripts python3 scripts/backup_credential.py resolve
"""

from auto_backup_credential import main

if __name__ == "__main__":
    raise SystemExit(main())
