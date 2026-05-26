"""Check key table columns."""
import sqlite3
conn = sqlite3.connect("shop.db")
c = conn.cursor()
for t in ['fake_reviews','user_balances','balance_topups','orders']:
    print(f'=== {t} ===')
    for r in c.execute(f'PRAGMA table_info({t})'):
        print(f'  {r[1]} ({r[2]})')
    cnt = c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
    print(f'  Count: {cnt}')
    print()
conn.close()
