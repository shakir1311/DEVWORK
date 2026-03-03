"""
One-time migration: recompute audit_log hashes from SHA-256 to HMAC-SHA256.

Walks the entire chain in order, recalculating record_hash and prev_hash
using the HMAC key from the LEDGER_HMAC_KEY environment variable.

Run from the Web/ directory:
    python migrate_hmac.py
"""

import sqlite3
import hashlib
import hmac
import os
from pathlib import Path

DB_PATH = os.path.join(os.path.dirname(__file__), "portal.db")

# Load .env from the same directory
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

_raw_key = os.getenv("LEDGER_HMAC_KEY")
if not _raw_key:
    raise RuntimeError(
        "LEDGER_HMAC_KEY is not set. "
        "Create Web/.env with: LEDGER_HMAC_KEY=<64-char hex secret>"
    )

LEDGER_HMAC_KEY = _raw_key.encode("utf-8")


def hmac_hash(prev_hash, timestamp, actor_id, action, details):
    payload = f"{prev_hash}{timestamp}{actor_id}{action}{details}"
    return hmac.new(LEDGER_HMAC_KEY, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def migrate():
    if not os.path.exists(DB_PATH):
        print("Database not found. Nothing to migrate.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, timestamp, actor_id, action, details FROM audit_log ORDER BY id ASC"
    )
    rows = cursor.fetchall()

    if not rows:
        print("No audit_log entries to migrate.")
        conn.close()
        return

    print(f"Migrating {len(rows)} audit_log entries to HMAC-SHA256...")

    prev_hash = "0" * 64
    updated = 0

    for row_id, timestamp, actor_id, action, details in rows:
        new_hash = hmac_hash(prev_hash, str(timestamp), actor_id, action, details)

        cursor.execute(
            "UPDATE audit_log SET prev_hash = ?, record_hash = ? WHERE id = ?",
            (prev_hash, new_hash, row_id),
        )

        prev_hash = new_hash
        updated += 1

    conn.commit()
    conn.close()

    print(f"Done. {updated} entries re-hashed with HMAC-SHA256.")
    print("Verify with: http://0.0.0.0:8000/api/audit/verify")


if __name__ == "__main__":
    migrate()
