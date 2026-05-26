"""Fix Turso: create fake_reviews table, clear products (FK fix), then full sync."""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

HTTP_URL = config.TURSO_URL.replace("libsql://", "https://")
TOKEN = config.TURSO_AUTH_TOKEN


def q(sql):
    body = json.dumps({"requests": [{"type": "execute", "stmt": {"sql": sql}}]}).encode()
    req = urllib.request.Request(HTTP_URL + "/v2/pipeline", data=body,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
    r = resp["results"][0]
    if r["type"] == "error":
        raise Exception(r["error"]["message"])


# 1. Create fake_reviews table
print("Creating fake_reviews table in Turso...")
try:
    q("""CREATE TABLE IF NOT EXISTS fake_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_name TEXT NOT NULL,
        review_text TEXT NOT NULL,
        rating INTEGER DEFAULT 5,
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    print("  Done!")
except Exception as e:
    print(f"  Error: {e}")

# 2. Disable FK, clear old data, re-enable FK
print("Clearing products + fake reviews from Turso (FK off)...")
try:
    q("PRAGMA foreign_keys = OFF")
    q("DELETE FROM products")
    q("DELETE FROM fake_reviews")
    q("PRAGMA foreign_keys = ON")
    print("  Done!")
except Exception as e:
    print(f"  Error: {e}")

print("Ready for full sync! Run sync_full_to_turso.py now.")
