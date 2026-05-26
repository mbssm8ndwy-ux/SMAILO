"""Sync fixed products back to Turso."""
import json, os, sqlite3, sys, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

def _turso_type(v):
    if v is None: return {"type": "null"}
    if isinstance(v, bool): return {"type": "integer", "value": "1" if v else "0"}
    if isinstance(v, int): return {"type": "integer", "value": str(v)}
    if isinstance(v, float): return {"type": "float", "value": float(v)}
    return {"type": "text", "value": str(v)}

HTTP_URL = config.TURSO_URL.replace("libsql://", "https://")
TOKEN = config.TURSO_AUTH_TOKEN

def q(sql, params=None):
    args = [_turso_type(p) for p in (params or [])]
    body = json.dumps({"requests": [{"type": "execute", "stmt": {"sql": sql, "args": args}}]}).encode()
    req = urllib.request.Request(HTTP_URL + "/v2/pipeline", data=body,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, timeout=300).read())
    r = resp["results"][0]
    if r["type"] == "error": raise Exception(r["error"]["message"])
    return r["response"]["result"]["rows"]

def q_pipeline(statements):
    requests = []
    for sql, params in statements:
        if params is not None:
            args = [_turso_type(p) for p in params]
            requests.append({"type": "execute", "stmt": {"sql": sql, "args": args}})
        else:
            requests.append({"type": "execute", "stmt": {"sql": sql}})
    body = json.dumps({"requests": requests}).encode()
    req = urllib.request.Request(HTTP_URL + "/v2/pipeline", data=body,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, timeout=300).read())
    for r in resp["results"]:
        if r["type"] == "error":
            raise Exception(r["error"]["message"])

# Delete and re-insert products (IDs 53+ only)
conn = sqlite3.connect("shop.db")
conn.row_factory = sqlite3.Row

print("Reading local products (ID >= 53)...")
rows = conn.execute("SELECT * FROM products WHERE id >= 53").fetchall()
pragma = conn.execute("PRAGMA table_info(products)").fetchall()
cols = [str(r[1]) for r in pragma]
col_str = ",".join(cols)
ph = ",".join("?" for _ in cols)
conn.close()

print(f"{len(rows)} products to update")

# Disable FK + delete + re-insert in batches
print("Updating Turso...")
q_pipeline([("PRAGMA foreign_keys = OFF", None), ("DELETE FROM products WHERE id >= 53", None)])

ok = 0
for i, row in enumerate(rows):
    vals = tuple(row[c] for c in cols)
    try:
        q(f"INSERT INTO products({col_str}) VALUES({ph})", vals)
        ok += 1
        if (i+1) % 25 == 0:
            print(f"  {i+1}/{len(rows)}")
    except Exception as e:
        print(f"  Error at row {i}: {e}")
        break

q_pipeline([("PRAGMA foreign_keys = ON", None)])
print(f"Done: {ok} products synced to Turso")
