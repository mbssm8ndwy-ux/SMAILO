"""Fast batch sync: insert all products + fake_reviews in single requests."""
import json, os, sqlite3, sys, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

def _turso_type(v):
    if v is None: return {"type": "null"}
    if isinstance(v, bool): return {"type": "integer", "value": "1" if v else "0"}
    if isinstance(v, int): return {"type": "integer", "value": str(v)}
    if isinstance(v, float): return {"type": "float", "value": v}
    return {"type": "text", "value": str(v)}

HTTP_URL = config.TURSO_URL.replace("libsql://", "https://")
TOKEN = config.TURSO_AUTH_TOKEN

def batch_insert(table, col_names, rows):
    """Insert multiple rows in a single pipeline request."""
    cols = ",".join(col_names)
    ph = ",".join("?" for _ in col_names)
    sql = f"INSERT OR IGNORE INTO {table}({cols}) VALUES({ph})"
    requests = []
    for row in rows:
        vals = tuple(row[c] for c in col_names)
        args = [_turso_type(v) for v in vals]
        requests.append({"type": "execute", "stmt": {"sql": sql, "args": args}})
    
    body = json.dumps({"requests": requests}).encode()
    req = urllib.request.Request(HTTP_URL + "/v2/pipeline", data=body,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, timeout=300).read())
    errors = [r for r in resp["results"] if r["type"] == "error"]
    return errors

def q_single(sql):
    body = json.dumps({"requests": [{"type": "execute", "stmt": {"sql": sql}}]}).encode()
    req = urllib.request.Request(HTTP_URL + "/v2/pipeline", data=body,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, timeout=120).read())
    r = resp["results"][0]
    if r["type"] == "error":
        raise Exception(r["error"]["message"])
    return r["response"]["result"]["rows"]

# Connect to local DB
print("Reading local shop.db...")
conn = sqlite3.connect("shop.db")
conn.row_factory = sqlite3.Row

# Get products and fake_reviews
product_rows = conn.execute("SELECT * FROM products").fetchall()
fake_review_rows = conn.execute("SELECT * FROM fake_reviews").fetchall()

conn.close()

# Batch insert products
if product_rows:
    conn2 = sqlite3.connect("shop.db")
    pragma = conn2.execute("PRAGMA table_info(products)").fetchall()
    prod_cols = [str(r[1]) for r in pragma]
    conn2.close()
    
    print(f"Batch inserting {len(product_rows)} products (5 per batch)...")
    errors = []
    for i in range(0, len(product_rows), 5):
        batch = product_rows[i:i+5]
        errs = batch_insert("products", prod_cols, batch)
        errors.extend(errs)
        if (i // 5) % 10 == 0:
            print(f"  batch {i//5 + 1}/{len(product_rows)//5 + 1}: {len(batch)} rows, errors: {len(errs)}")
        if errs:
            print(f"  ERROR at batch {i//5 + 1}: {errs[0]['error']['message']}")
            break

# Batch insert fake reviews
if fake_review_rows:
    conn3 = sqlite3.connect("shop.db")
    pragma = conn3.execute("PRAGMA table_info(fake_reviews)").fetchall()
    rev_cols = [str(r[1]) for r in pragma]
    conn3.close()
    
    print(f"Inserting {len(fake_review_rows)} fake reviews...")
    errors = batch_insert("fake_reviews", rev_cols, fake_review_rows)
    if errors:
        for e in errors:
            print(f"  Error: {e['error']['message']}")
    else:
        print(f"  All {len(fake_review_rows)} reviews inserted!")

# Verify
print("\nVerification:")
for table in ["products", "fake_reviews"]:
    cnt = q_single(f"SELECT COUNT(*) FROM {table}")
    print(f"  {table}: {cnt[0]['value']}")

# Re-enable FK
print("Re-enabling FK...")
q_single("PRAGMA foreign_keys = ON")
print("Done!")
