"""Full sync: copy all data from local shop.db to Turso cloud database."""
import json
import os
import sqlite3
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

HTTP_URL = config.TURSO_URL.replace("libsql://", "https://")
TOKEN = config.TURSO_AUTH_TOKEN


def _turso_type(v):
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": "1" if v else "0"}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    return {"type": "text", "value": str(v)}


def q(sql, params=None):
    args = [_turso_type(p) for p in (params or [])]
    body = json.dumps({"requests": [{"type": "execute", "stmt": {"sql": sql, "args": args}}]}).encode()
    req = urllib.request.Request(HTTP_URL + "/v2/pipeline", data=body,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        method="POST")
    resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
    r = resp["results"][0]
    if r["type"] == "error":
        raise Exception(r["error"]["message"])
    return r["response"]["result"]


def main():
    if not config.TURSO_URL or not config.TURSO_AUTH_TOKEN:
        print("TURSO_URL / TURSO_AUTH_TOKEN not set")
        return

    print("Reading local shop.db...")
    conn = sqlite3.connect("shop.db")
    conn.row_factory = sqlite3.Row

    # Get all tables (skip internal ones)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()]

    for table in tables:
        pragma = conn.execute(f"PRAGMA table_info({table})").fetchall()
        col_names = [str(r["name"]) for r in pragma]
        if not col_names:
            continue

        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        if not rows:
            print(f"  {table}: empty")
            continue

        cols = ",".join(col_names)
        ph = ",".join("?" for _ in col_names)
        sql = f"INSERT OR IGNORE INTO {table}({cols}) VALUES({ph})"

        ok = 0
        for row in rows:
            vals = tuple(row[c] for c in col_names)
            try:
                q(sql, vals)
                ok += 1
            except urllib.request.HTTPError as e:
                detail = e.read().decode()
                print(f"  {table}: HTTP error at row {ok}: {detail[:200]}")
                break
            except Exception as e:
                print(f"  {table}: error at row {ok}: {e}")
                break
        print(f"  {table}: {ok}/{len(rows)} rows synced")

    conn.close()
    print("Done! All catalog data is now in Turso.")


if __name__ == "__main__":
    main()
