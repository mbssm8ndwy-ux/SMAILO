"""Add Yalta districts directly to Turso via HTTP API."""
import json
import urllib.request

HTTP_URL = "https://smailo-mbssm8ndwy-ux.aws-ap-northeast-1.turso.io/v2/pipeline"
TOKEN = "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJleHAiOjE4MTEzMjgwNDksImlhdCI6MTc3OTc5MjA0OSwiaWQiOiIwMTllNjNkZS1lODAxLTc2YTAtODJmMS05MTVhOGNmMjVkNDgiLCJyaWQiOiI3ZmRlNDAyNC1lMjc2LTRhODQtYWU0Yi1lNWEwNzk1YjMwZDYifQ.3ArMECXJDhYO2A-ePYzLvw3_bjbU13J93HEDZVaDpNySFMF0X-4TuqVhhvRE1Y9fGIYgfYkIdrZsNcK_vn5VBA"

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
    req = urllib.request.Request(HTTP_URL, data=body,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        method="POST")
    resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
    return resp

# Check Yalta city exists
r = q("SELECT id, name FROM cities WHERE name = 'Ялта'")
result = r["results"][0]["response"]["result"]
rows = result["rows"]
if rows:
    # Row cells are typed: {"type": "integer", "value": "1"}
    city_id = int(rows[0][0]["value"])
    print(f"Found city: Ялта (id={city_id})")
else:
    print("City Ялта not found in Turso! You need to add cities first through admin panel.")
    exit()

# Add districts
districts = [
    "ул.Кривошты",
    "ул.Руданского (Старый город)",
    "ул.Найденова (10мкрн)",
    "ул.Соханя (р-н Васильевка)",
    "ул.Строителей",
    "ул.Мисхорская",
]

for d in districts:
    r = q("INSERT OR IGNORE INTO districts(city_id, name) VALUES(?, ?)", (city_id, d))
    print(f"  {d}: {'OK' if r['results'][0]['type'] == 'ok' else 'FAIL'}")
