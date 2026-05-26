"""Read local shop.db and sync all data to Turso cloud database."""
import json
import os
import sqlite3
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TURSO_HTTP = ""
TURSO_TOKEN = ""


def turso(sql, params=None):
    HTTP_URL = TURSO_HTTP
    TOKEN = TURSO_TOKEN
    args = []
    if params:
        for p in params:
            if p is None:
                args.append({"type": "null"})
            elif isinstance(p, bool):
                args.append({"type": "integer", "value": "1" if p else "0"})
            elif isinstance(p, int):
                args.append({"type": "integer", "value": str(p)})
            elif isinstance(p, float):
                args.append({"type": "float", "value": str(p)})
            else:
                args.append({"type": "text", "value": str(p)})
    body = json.dumps({"requests": [{"type": "execute", "stmt": {"sql": sql, "args": args}}]}).encode()
    req = urllib.request.Request(
        HTTP_URL + "/v2/pipeline", data=body,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
    r = resp.get("results", [{}])[0]
    if r.get("type") == "error":
        raise Exception(r["error"]["message"])


def main():
    import config
    if not config.TURSO_URL or not config.TURSO_AUTH_TOKEN:
        print("TURSO_URL and TURSO_AUTH_TOKEN must be set")
        return

    global TURSO_HTTP, TURSO_TOKEN
    TURSO_HTTP = config.TURSO_URL.replace("libsql://", "https://")
    TURSO_TOKEN = config.TURSO_AUTH_TOKEN

    conn = sqlite3.connect("shop.db")
    conn.row_factory = sqlite3.Row

    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]

    for table in tables:
        # Get column names from pragma
        pragma = conn.execute(f"PRAGMA table_info({table})").fetchall()
        col_names = [str(r["name"]) for r in pragma]
        if not col_names:
            continue

        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        if not rows:
            print(f"  {table}: empty, skipping")
            continue

        # Build INSERT with placeholders
        cols = ",".join(col_names)
        ph = ",".join("?" for _ in col_names)
        sql = f"INSERT OR IGNORE INTO {table}({cols}) VALUES({ph})"

        count = 0
        for row in rows:
            vals = tuple(row[c] for c in col_names)
            try:
                turso(sql, vals)
                count += 1
            except Exception as e:
                print(f"  {table}: error at row {count}: {e}")
                break
        print(f"  {table}: synced {count}/{len(rows)} rows")

    conn.close()
    print("Done!")


if __name__ == "__main__":
    main()
