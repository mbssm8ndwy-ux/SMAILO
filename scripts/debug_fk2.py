"""Verify district 136 exists in Turso."""
import json, os, sys, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

def q(sql):
    args = []
    body = json.dumps({"requests": [{"type": "execute", "stmt": {"sql": sql, "args": args}}]}).encode()
    req = urllib.request.Request(config.TURSO_URL.replace("libsql://", "https://") + "/v2/pipeline", data=body,
        headers={"Authorization": f"Bearer {config.TURSO_AUTH_TOKEN}", "Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
    r = resp["results"][0]
    if r["type"] == "error":
        raise Exception(r["error"]["message"])
    return r["response"]["result"]["rows"]

# Check district 136
print("Checking district 136 in Turso...")
try:
    rows = q("SELECT * FROM districts WHERE id=136")
    print(f"Result: {rows}")
except Exception as e:
    print(f"Error: {e}")

# Try inserting the problematic product manually with more verbose error
print("\nTrying to insert product 73 manually...")
try:
    body = json.dumps({"requests": [{"type": "execute", "stmt": {"sql": "INSERT INTO products(district_id, assortment_id, title, price, qty_value, qty_unit) VALUES(136, 6, 'Test Product', 1000, 1, 'g')"}}]}).encode()
    req = urllib.request.Request(config.TURSO_URL.replace("libsql://", "https://") + "/v2/pipeline", data=body,
        headers={"Authorization": f"Bearer {config.TURSO_AUTH_TOKEN}", "Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
    print(resp)
except Exception as e:
    print(f"Error: {e}")
