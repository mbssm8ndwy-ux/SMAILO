"""Check what's in Turso."""
import json, os, sys, urllib.request
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
    return r["response"]["result"]["rows"]

print("=== ASSORTMENTS ===")
for row in q("SELECT * FROM assortments"):
    print(row)

print("\n=== PRODUCTS ===")
for row in q("SELECT id, title, assortment_id, price FROM products"):
    print(row)
