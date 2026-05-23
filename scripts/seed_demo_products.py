"""
Тестовые позиции: «Шарики» (шт) и «Снежинки» (г) в районе Симферополь · Центральный.

Запуск: python scripts/seed_demo_products.py
Переопределить район: set DEMO_DISTRICT_ID=12 (переменная окружения).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from db import Database  # noqa: E402


def _find_simferopol_central(db: Database) -> int | None:
    for c in db.get_cities():
        if "Симферополь" not in c["name"]:
            continue
        for d in db.get_districts(c["id"]):
            if d["name"] == "Центральный":
                return int(d["id"])
    return None


def main() -> None:
    db = Database()
    did = os.environ.get("DEMO_DISTRICT_ID")
    district_id = int(did) if did and did.isdigit() else (_find_simferopol_central(db) or 0)
    if not district_id:
        print("Не найден район «Симферополь / Центральный». Задайте DEMO_DISTRICT_ID.")
        sys.exit(1)

    demo = [
        ("Шарики", "Тестовые шарики ассорти", 199.0, 12.0, "шт"),
        ("Снежинки", "Тестовые снежинки сыпучие", 149.0, 2.5, "г"),
    ]
    for ass, title, price, qv, qu in demo:
        ok = db.add_product(
            district_id,
            ass,
            title,
            price,
            None,
            qty_value=qv,
            qty_unit=qu,
        )
        print(f"{'OK' if ok else 'SKIP/DUP'} district={district_id} | {ass} | {title} | {price} | {qv}{qu}")

    names = db.get_assortment_names()
    print("Ассортименты в каталоге:", ", ".join(names) if names else "(пусто)")


if __name__ == "__main__":
    main()
