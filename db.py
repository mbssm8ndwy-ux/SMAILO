import sqlite3
from typing import Dict, List, Optional

MAX_PAYMENT_CARDS = 10
MAX_PAYMENT_SBP = 10


class Database:
    def __init__(self, path: str = "shop.db") -> None:
        self.path = path
        self._init_db()

    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS cities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS districts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    city_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    UNIQUE(city_id, name),
                    FOREIGN KEY(city_id) REFERENCES cities(id) ON DELETE CASCADE
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS assortments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    district_id INTEGER NOT NULL,
                    assortment_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    price REAL NOT NULL,
                    qty_value REAL NOT NULL DEFAULT 1,
                    qty_unit TEXT NOT NULL DEFAULT 'шт',
                    auto_delivery_url TEXT,
                    UNIQUE(district_id, assortment_id, title),
                    FOREIGN KEY(district_id) REFERENCES districts(id) ON DELETE CASCADE,
                    FOREIGN KEY(assortment_id) REFERENCES assortments(id) ON DELETE CASCADE
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS payment_cards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    details TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS payment_sbp (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    details TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS payment_rotation (
                    kind TEXT PRIMARY KEY,
                    counter INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    chat_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    pay_method TEXT NOT NULL,
                    requisite_id INTEGER,
                    requisite_text TEXT NOT NULL,
                    amount REAL NOT NULL,
                    status TEXT NOT NULL,
                    delivery_links TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(product_id) REFERENCES products(id)
                )
                """
            )
            self._migrate_schema(conn)
            conn.commit()

    def _migrate_schema(self, conn) -> None:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        cur.execute("PRAGMA table_info(products)")
        colnames = [row[1] for row in cur.fetchall()]
        if "auto_delivery_url" not in colnames:
            cur.execute("ALTER TABLE products ADD COLUMN auto_delivery_url TEXT")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_balances (
                user_id INTEGER PRIMARY KEY,
                balance REAL NOT NULL DEFAULT 0
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS balance_topups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS referrals (
                invited_user_id INTEGER PRIMARY KEY,
                referrer_id INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS topup_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                requisite_text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'awaiting_claim',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS support_tickets (
                log_message_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
                last_seen TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for _sql in (
            "INSERT OR IGNORE INTO bot_users(user_id) SELECT DISTINCT user_id FROM orders",
            "INSERT OR IGNORE INTO bot_users(user_id) SELECT DISTINCT invited_user_id FROM referrals",
            "INSERT OR IGNORE INTO bot_users(user_id) SELECT DISTINCT referrer_id FROM referrals",
            "INSERT OR IGNORE INTO bot_users(user_id) SELECT DISTINCT user_id FROM user_balances",
            "INSERT OR IGNORE INTO bot_users(user_id) SELECT DISTINCT user_id FROM topup_requests",
        ):
            cur.execute(_sql)
        cur.execute("PRAGMA table_info(orders)")
        ord_cols = [row[1] for row in cur.fetchall()]
        if "review_text" not in ord_cols:
            cur.execute("ALTER TABLE orders ADD COLUMN review_text TEXT")
        if "review_at" not in ord_cols:
            cur.execute("ALTER TABLE orders ADD COLUMN review_at TEXT")
        if "review_rating" not in ord_cols:
            cur.execute("ALTER TABLE orders ADD COLUMN review_rating INTEGER")
        cur.execute("PRAGMA table_info(balance_topups)")
        bt_cols = [row[1] for row in cur.fetchall()]
        if "source" not in bt_cols:
            cur.execute(
                "ALTER TABLE balance_topups ADD COLUMN source TEXT DEFAULT 'topup'"
            )
        self._migrate_to_assortments_model(conn)
        self._migrate_product_qty_columns(conn)
        self._migrate_drop_product_title_unique(conn)
        self._migrate_captcha_column(conn)

    def _migrate_product_qty_columns(self, conn) -> None:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(products)")
        cols = [row[1] for row in cur.fetchall()]
        if "qty_value" in cols:
            return
        cur.execute(
            "ALTER TABLE products ADD COLUMN qty_value REAL NOT NULL DEFAULT 1"
        )
        cur.execute(
            "ALTER TABLE products ADD COLUMN qty_unit TEXT NOT NULL DEFAULT 'шт'"
        )
        cur.execute("UPDATE products SET qty_value = 1 WHERE qty_value IS NULL")
        cur.execute("UPDATE products SET qty_unit = 'шт' WHERE qty_unit IS NULL OR TRIM(qty_unit) = ''")

    def _migrate_drop_product_title_unique(self, conn) -> None:
        """
        Снимает уникальность (district_id, assortment_id, title),
        чтобы можно было добавлять одинаковые названия позиций.
        """
        cur = conn.cursor()
        row = cur.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='products'"
        ).fetchone()
        if not row or not row[0]:
            return
        table_sql = str(row[0]).upper()
        if "UNIQUE(DISTRICT_ID, ASSORTMENT_ID, TITLE)" not in table_sql:
            return

        conn.execute("PRAGMA foreign_keys = OFF")
        cur.execute(
            """
            CREATE TABLE products__nodup (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                district_id INTEGER NOT NULL,
                assortment_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                price REAL NOT NULL,
                qty_value REAL NOT NULL DEFAULT 1,
                qty_unit TEXT NOT NULL DEFAULT 'шт',
                auto_delivery_url TEXT,
                FOREIGN KEY(district_id) REFERENCES districts(id) ON DELETE CASCADE,
                FOREIGN KEY(assortment_id) REFERENCES assortments(id) ON DELETE CASCADE
            )
            """
        )
        cur.execute(
            """
            INSERT INTO products__nodup(
                id, district_id, assortment_id, title, price,
                qty_value, qty_unit, auto_delivery_url
            )
            SELECT id, district_id, assortment_id, title, price,
                   COALESCE(qty_value, 1),
                   COALESCE(NULLIF(TRIM(qty_unit), ''), 'шт'),
                   auto_delivery_url
            FROM products
            ORDER BY id ASC
            """
        )
        cur.execute("DROP TABLE products")
        cur.execute("ALTER TABLE products__nodup RENAME TO products")
        conn.execute("PRAGMA foreign_keys = ON")

    def _migrate_to_assortments_model(self, conn) -> None:
        """Старый формат: одно поле name у позиции → ассортимент + title позиции."""
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS assortments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            )
            """
        )
        cur.execute("PRAGMA table_info(products)")
        cols = [row[1] for row in cur.fetchall()]
        if not cols:
            return
        if "assortment_id" in cols:
            return
        if "name" not in cols:
            return
        cur.execute(
            """
            CREATE TABLE products__am (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                district_id INTEGER NOT NULL,
                assortment_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                price REAL NOT NULL,
                qty_value REAL NOT NULL DEFAULT 1,
                qty_unit TEXT NOT NULL DEFAULT 'шт',
                auto_delivery_url TEXT,
                UNIQUE(district_id, assortment_id, title),
                FOREIGN KEY(district_id) REFERENCES districts(id) ON DELETE CASCADE,
                FOREIGN KEY(assortment_id) REFERENCES assortments(id) ON DELETE CASCADE
            )
            """
        )
        cur.execute(
            """
            INSERT OR IGNORE INTO assortments(name)
            SELECT DISTINCT TRIM(name) FROM products
            WHERE TRIM(COALESCE(name, '')) != ''
            """
        )
        cur.execute(
            """
            INSERT INTO products__am(id, district_id, assortment_id, title, price, qty_value, qty_unit, auto_delivery_url)
            SELECT p.id, p.district_id, a.id, TRIM(p.name), p.price, 1, 'шт', p.auto_delivery_url
            FROM products p
            INNER JOIN assortments a ON a.name = TRIM(p.name)
            """
        )
        cur.execute("DROP TABLE products")
        cur.execute("ALTER TABLE products__am RENAME TO products")

    @staticmethod
    def _rows_to_dicts(rows) -> List[Dict]:
        return [dict(row) for row in rows]

    def get_cities(self) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT id, name FROM cities ORDER BY name").fetchall()
            return self._rows_to_dicts(rows)

    def add_city(self, name: str) -> bool:
        with self._connect() as conn:
            try:
                conn.execute("INSERT INTO cities(name) VALUES (?)", (name.strip(),))
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def delete_city(self, city_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM cities WHERE id = ?", (city_id,))
            conn.commit()
            return cur.rowcount > 0

    def get_districts(self, city_id: int) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, city_id, name FROM districts WHERE city_id = ? ORDER BY name",
                (city_id,),
            ).fetchall()
            return self._rows_to_dicts(rows)

    def add_district(self, city_id: int, name: str) -> bool:
        with self._connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO districts(city_id, name) VALUES (?, ?)",
                    (city_id, name.strip()),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def delete_district(self, district_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM districts WHERE id = ?", (district_id,))
            conn.commit()
            return cur.rowcount > 0

    def get_assortment_names(self) -> List[str]:
        """Имена ассортиментов, по которым есть хотя бы одна позиция в каталоге."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT a.name
                FROM assortments a
                INNER JOIN products p ON p.assortment_id = a.id
                ORDER BY a.name COLLATE NOCASE
                """
            ).fetchall()
            return [str(r["name"]) for r in rows]

    def get_cities_for_assortment_name(self, assortment_name: str) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT c.id, c.name
                FROM cities c
                INNER JOIN districts d ON d.city_id = c.id
                INNER JOIN products p ON p.district_id = d.id
                INNER JOIN assortments a ON a.id = p.assortment_id
                WHERE a.name = ?
                ORDER BY c.name COLLATE NOCASE
                """,
                (assortment_name.strip(),),
            ).fetchall()
            return self._rows_to_dicts(rows)

    def get_districts_for_assortment_in_city(
        self, assortment_name: str, city_id: int
    ) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT d.id, d.city_id, d.name
                FROM districts d
                INNER JOIN products p ON p.district_id = d.id
                INNER JOIN assortments a ON a.id = p.assortment_id
                WHERE d.city_id = ? AND a.name = ?
                ORDER BY d.name COLLATE NOCASE
                """,
                (city_id, assortment_name.strip()),
            ).fetchall()
            return self._rows_to_dicts(rows)

    def list_positions_by_district_assortment(
        self, district_id: int, assortment_name: str
    ) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT p.id, p.district_id, p.assortment_id, p.title, p.price,
                       p.qty_value, p.qty_unit, p.auto_delivery_url,
                       a.name AS assortment_name
                FROM products p
                INNER JOIN assortments a ON a.id = p.assortment_id
                WHERE p.district_id = ? AND a.name = ?
                ORDER BY p.id ASC
                """,
                (district_id, assortment_name.strip()),
            ).fetchall()
            return self._rows_to_dicts(rows)

    def list_user_orders(self, user_id: int, limit: int = 15) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT o.id, o.amount, o.status, o.created_at, o.delivery_links,
                       o.review_text,
                       COALESCE(
                           a.name || ' · ' || p.title || ' · '
                           || printf('%g', COALESCE(p.qty_value, 1))
                           || ' ' || COALESCE(NULLIF(TRIM(p.qty_unit), ''), 'шт'),
                           '(товар удалён)'
                       ) AS product_name
                FROM orders o
                LEFT JOIN products p ON p.id = o.product_id
                LEFT JOIN assortments a ON a.id = p.assortment_id
                WHERE o.user_id = ?
                ORDER BY o.id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
            return self._rows_to_dicts(rows)

    def get_products(self, district_id: int) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT p.id, p.district_id, p.assortment_id, p.title, p.price,
                       p.qty_value, p.qty_unit, p.auto_delivery_url,
                       a.name AS assortment_name
                FROM products p
                INNER JOIN assortments a ON a.id = p.assortment_id
                WHERE p.district_id = ?
                ORDER BY a.name COLLATE NOCASE, p.title COLLATE NOCASE
                """,
                (district_id,),
            ).fetchall()
            return self._rows_to_dicts(rows)

    def list_product_templates(self, limit: int = 40) -> List[Dict]:
        """
        Шаблоны позиций (уникальные по ассортименту/названию/кол-ву/автоссылке),
        собранные из существующих товаров.
        """
        lim = max(1, min(int(limit), 200))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    MIN(p.id) AS template_product_id,
                    a.name AS assortment_name,
                    p.title,
                    COALESCE(p.qty_value, 1) AS qty_value,
                    COALESCE(NULLIF(TRIM(p.qty_unit), ''), 'шт') AS qty_unit,
                    COALESCE(p.auto_delivery_url, '') AS auto_delivery_url,
                    COUNT(*) AS variants_count
                FROM products p
                INNER JOIN assortments a ON a.id = p.assortment_id
                GROUP BY a.name, p.title,
                         COALESCE(p.qty_value, 1),
                         COALESCE(NULLIF(TRIM(p.qty_unit), ''), 'шт'),
                         COALESCE(p.auto_delivery_url, '')
                ORDER BY a.name COLLATE NOCASE, p.title COLLATE NOCASE
                LIMIT ?
                """,
                (lim,),
            ).fetchall()
            return self._rows_to_dicts(rows)

    def list_assortments(self) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT a.id, a.name,
                       COUNT(p.id) AS product_count
                FROM assortments a
                LEFT JOIN products p ON p.assortment_id = a.id
                GROUP BY a.id, a.name
                ORDER BY a.name COLLATE NOCASE
                """
            ).fetchall()
            return self._rows_to_dicts(rows)

    def add_assortment(self, name: str) -> bool:
        """Добавить ассортимент по имени (уникальное). Пустая строка — False."""
        n = name.strip()
        if not n:
            return False
        with self._connect() as conn:
            try:
                conn.execute("INSERT INTO assortments(name) VALUES (?)", (n,))
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def delete_assortment_if_empty(self, assortment_id: int) -> bool:
        """Удалить ассортимент, только если нет ни одной позиции."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM products WHERE assortment_id = ?",
                (assortment_id,),
            ).fetchone()
            if row and int(row["c"]) > 0:
                return False
            cur = conn.execute("DELETE FROM assortments WHERE id = ?", (assortment_id,))
            conn.commit()
            return cur.rowcount > 0

    def _get_or_create_assortment_id(self, conn, assortment_name: str) -> int:
        n = assortment_name.strip()
        row = conn.execute("SELECT id FROM assortments WHERE name = ?", (n,)).fetchone()
        if row:
            return int(row["id"])
        cur = conn.execute("INSERT INTO assortments(name) VALUES (?)", (n,))
        return int(cur.lastrowid)

    def add_product(
        self,
        district_id: int,
        assortment_name: str,
        title: str,
        price: float,
        auto_delivery_url: Optional[str] = None,
        *,
        qty_value: float = 1.0,
        qty_unit: str = "шт",
    ) -> bool:
        """Позиция: district_id + ассортимент + название + цена; qty — количество в шт или граммах."""
        url = auto_delivery_url.strip() if auto_delivery_url else None
        if url == "":
            url = None
        qu = (qty_unit or "шт").strip().lower()
        if qu in ("g", "г"):
            qu = "г"
        else:
            qu = "шт"
        qv = float(qty_value)
        if qv <= 0:
            return False
        with self._connect() as conn:
            try:
                aid = self._get_or_create_assortment_id(conn, assortment_name)
                conn.execute(
                    """
                    INSERT INTO products(
                        district_id, assortment_id, title, price, qty_value, qty_unit, auto_delivery_url
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (district_id, aid, title.strip(), price, qv, qu, url),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def update_product_price(self, product_id: int, price: float) -> bool:
        with self._connect() as conn:
            cur = conn.execute("UPDATE products SET price = ? WHERE id = ?", (price, product_id))
            conn.commit()
            return cur.rowcount > 0

    def delete_product(self, product_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
            conn.commit()
            return cur.rowcount > 0

    def get_city(self, city_id: int) -> Optional[Dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT id, name FROM cities WHERE id = ?", (city_id,)).fetchone()
            return dict(row) if row else None

    def get_district(self, district_id: int) -> Optional[Dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, city_id, name FROM districts WHERE id = ?",
                (district_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_product(self, product_id: int) -> Optional[Dict]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT p.id, p.district_id, p.assortment_id, p.title, p.price,
                       p.qty_value, p.qty_unit, p.auto_delivery_url,
                       a.name AS assortment_name
                FROM products p
                INNER JOIN assortments a ON a.id = p.assortment_id
                WHERE p.id = ?
                """,
                (product_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_setting(self, key: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM bot_settings WHERE key = ?", (key,)
            ).fetchone()
            if not row or row["value"] is None:
                return None
            v = str(row["value"]).strip()
            return v if v else None

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO bot_settings(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            conn.commit()

    def delete_setting(self, key: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM bot_settings WHERE key = ?", (key,))
            conn.commit()

    def count_payment_cards(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM payment_cards").fetchone()
            return int(row["c"]) if row else 0

    def count_payment_sbp(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM payment_sbp").fetchone()
            return int(row["c"]) if row else 0

    def list_payment_cards(self) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, details FROM payment_cards ORDER BY id ASC"
            ).fetchall()
            return self._rows_to_dicts(rows)

    def list_payment_sbp(self) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, details FROM payment_sbp ORDER BY id ASC"
            ).fetchall()
            return self._rows_to_dicts(rows)

    def add_payment_card(self, details: str) -> bool:
        if self.count_payment_cards() >= MAX_PAYMENT_CARDS:
            return False
        with self._connect() as conn:
            conn.execute("INSERT INTO payment_cards(details) VALUES (?)", (details.strip(),))
            conn.commit()
            return True

    def add_payment_sbp(self, details: str) -> bool:
        if self.count_payment_sbp() >= MAX_PAYMENT_SBP:
            return False
        with self._connect() as conn:
            conn.execute("INSERT INTO payment_sbp(details) VALUES (?)", (details.strip(),))
            conn.commit()
            return True

    def delete_payment_card(self, card_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM payment_cards WHERE id = ?", (card_id,))
            conn.commit()
            return cur.rowcount > 0

    def delete_payment_sbp(self, sbp_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM payment_sbp WHERE id = ?", (sbp_id,))
            conn.commit()
            return cur.rowcount > 0

    def _advance_pick_index(self, kind: str, modulo: int) -> int:
        if modulo <= 0:
            return 0
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO payment_rotation(kind, counter) VALUES (?, 0)",
                (kind,),
            )
            row = conn.execute(
                "SELECT counter FROM payment_rotation WHERE kind = ?",
                (kind,),
            ).fetchone()
            c = int(row["counter"]) if row else 0
            idx = c % modulo
            conn.execute(
                "UPDATE payment_rotation SET counter = counter + 1 WHERE kind = ?",
                (kind,),
            )
            conn.commit()
            return idx

    def pick_next_payment_card(self) -> Optional[Dict]:
        cards = self.list_payment_cards()
        if not cards:
            return None
        idx = self._advance_pick_index("card", len(cards))
        return cards[idx]

    def pick_next_payment_sbp(self) -> Optional[Dict]:
        items = self.list_payment_sbp()
        if not items:
            return None
        idx = self._advance_pick_index("sbp", len(items))
        return items[idx]

    def create_order(
        self,
        user_id: int,
        username: Optional[str],
        chat_id: int,
        product_id: int,
        pay_method: str,
        requisite_id: Optional[int],
        requisite_text: str,
        amount: float,
        status: str = "awaiting_payment",
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO orders(
                    user_id, username, chat_id, product_id, pay_method,
                    requisite_id, requisite_text, amount, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    username,
                    chat_id,
                    product_id,
                    pay_method,
                    requisite_id,
                    requisite_text,
                    amount,
                    status,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def create_order_paid_by_balance(
        self,
        user_id: int,
        username: Optional[str],
        chat_id: int,
        product_id: int,
        amount: float,
    ) -> Optional[int]:
        with self._connect() as conn:
            self._ensure_balance_row(conn, user_id)
            row = conn.execute(
                "SELECT balance FROM user_balances WHERE user_id = ?", (user_id,)
            ).fetchone()
            bal = float(row["balance"]) if row else 0.0
            if bal + 1e-9 < amount:
                return None
            cur = conn.execute(
                """
                UPDATE user_balances SET balance = balance - ?
                WHERE user_id = ? AND balance >= ?
                """,
                (amount, user_id, amount),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return None
            req_txt = f"Оплата с баланса ({amount:.2f} ₽)"
            cur = conn.execute(
                """
                INSERT INTO orders(
                    user_id, username, chat_id, product_id, pay_method,
                    requisite_id, requisite_text, amount, status
                ) VALUES (?, ?, ?, ?, 'balance', NULL, ?, ?, 'awaiting_payment')
                """,
                (
                    user_id,
                    username,
                    chat_id,
                    product_id,
                    req_txt,
                    amount,
                ),
            )
            order_id = int(cur.lastrowid)
            conn.execute(
                """
                INSERT INTO balance_topups(user_id, amount, source)
                VALUES (?, ?, 'purchase')
                """,
                (user_id, -amount),
            )
            conn.commit()
            return order_id

    def refund_order_balance(self, user_id: int, amount: float) -> None:
        if amount <= 0:
            return
        with self._connect() as conn:
            self._ensure_balance_row(conn, user_id)
            conn.execute(
                "UPDATE user_balances SET balance = balance + ? WHERE user_id = ?",
                (amount, user_id),
            )
            conn.execute(
                """
                INSERT INTO balance_topups(user_id, amount, source)
                VALUES (?, ?, 'refund')
                """,
                (user_id, amount),
            )
            conn.commit()

    def admin_credit_balance(self, user_id: int, amount: float) -> None:
        if amount <= 0:
            raise ValueError("amount must be positive")
        with self._connect() as conn:
            self._ensure_balance_row(conn, user_id)
            conn.execute(
                "UPDATE user_balances SET balance = balance + ? WHERE user_id = ?",
                (amount, user_id),
            )
            conn.execute(
                """
                INSERT INTO balance_topups(user_id, amount, source)
                VALUES (?, ?, 'admin_credit')
                """,
                (user_id, amount),
            )
            conn.commit()

    def admin_try_debit_balance(self, user_id: int, amount: float) -> bool:
        if amount <= 0:
            return False
        with self._connect() as conn:
            self._ensure_balance_row(conn, user_id)
            row = conn.execute(
                "SELECT balance FROM user_balances WHERE user_id = ?", (user_id,)
            ).fetchone()
            bal = float(row["balance"]) if row else 0.0
            if bal + 1e-9 < amount:
                return False
            cur = conn.execute(
                """
                UPDATE user_balances SET balance = balance - ?
                WHERE user_id = ? AND balance >= ?
                """,
                (amount, user_id, amount),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return False
            conn.execute(
                """
                INSERT INTO balance_topups(user_id, amount, source)
                VALUES (?, ?, 'admin_debit')
                """,
                (user_id, -amount),
            )
            conn.commit()
            return True

    def get_order(self, order_id: int) -> Optional[Dict]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, user_id, username, chat_id, product_id, pay_method,
                       requisite_id, requisite_text, amount, status, delivery_links,
                       review_text, review_at, review_rating, created_at
                FROM orders WHERE id = ?
                """,
                (order_id,),
            ).fetchone()
            return dict(row) if row else None

    def set_order_status(self, order_id: int, status: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE orders SET status = ? WHERE id = ?",
                (status, order_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def complete_order(self, order_id: int, delivery_links: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE orders SET status = 'completed', delivery_links = ?
                WHERE id = ? AND status = 'pending_confirm'
                """,
                (delivery_links.strip(), order_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def complete_order_from_awaiting(self, order_id: int, delivery_links: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE orders SET status = 'completed', delivery_links = ?
                WHERE id = ? AND status = 'awaiting_payment'
                """,
                (delivery_links.strip(), order_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def save_order_review(
        self, order_id: int, user_id: int, text: str, rating: int
    ) -> bool:
        t = (text or "").strip()
        r = int(rating)
        if not t or len(t) > 4000 or r < 1 or r > 5:
            return False
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE orders
                SET review_text = ?, review_at = datetime('now'), review_rating = ?
                WHERE id = ? AND user_id = ? AND status = 'completed'
                AND (review_text IS NULL OR TRIM(review_text) = '')
                """,
                (t, r, order_id, user_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def list_public_reviews(self, limit: int = 20) -> List[Dict]:
        """Публичная витрина отзывов: анонимно, только город/позиция/текст."""
        lim = max(1, min(int(limit), 100))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT o.id AS order_id,
                       o.review_text,
                       COALESCE(o.review_rating, 0) AS review_rating,
                       COALESCE(o.review_at, o.created_at) AS published_at,
                       COALESCE(c.name, '—') AS city_name,
                       COALESCE(
                           p.title || ' ('
                           || printf('%g', COALESCE(p.qty_value, 1))
                           || ' ' || COALESCE(NULLIF(TRIM(p.qty_unit), ''), 'шт')
                           || ')',
                           '(позиция удалена)'
                       ) AS product_title
                FROM orders o
                LEFT JOIN products p ON p.id = o.product_id
                LEFT JOIN districts d ON d.id = p.district_id
                LEFT JOIN cities c ON c.id = d.city_id
                WHERE o.status = 'completed'
                  AND TRIM(COALESCE(o.review_text, '')) != ''
                ORDER BY COALESCE(o.review_at, o.created_at) DESC, o.id DESC
                LIMIT ?
                """,
                (lim,),
            ).fetchall()
            return self._rows_to_dicts(rows)

    def get_pending_confirm_orders(self) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT o.id, o.user_id, o.username, o.product_id, o.pay_method,
                       o.requisite_text, o.amount, o.created_at,
                       COALESCE(
                           a.name || ' · ' || p.title || ' · '
                           || printf('%g', COALESCE(p.qty_value, 1))
                           || ' ' || COALESCE(NULLIF(TRIM(p.qty_unit), ''), 'шт'),
                           '(товар удалён)'
                       ) AS product_name
                FROM orders o
                LEFT JOIN products p ON p.id = o.product_id
                LEFT JOIN assortments a ON a.id = p.assortment_id
                WHERE o.status = 'pending_confirm'
                ORDER BY o.id ASC
                """
            ).fetchall()
            return self._rows_to_dicts(rows)

    def clear_all_orders(self) -> int:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM orders")
            conn.commit()
            return cur.rowcount

    def reset_catalog(self, *, clear_orders: bool = True) -> Dict[str, int]:
        """
        Удаляет все товары, районы и города.
        Заказы по умолчанию очищаются (ссылка product_id иначе мешает удалению товаров).
        """
        with self._connect() as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            stats: Dict[str, int] = {}
            if clear_orders:
                cur = conn.execute("DELETE FROM orders")
                stats["orders_deleted"] = cur.rowcount
            cur = conn.execute("DELETE FROM products")
            stats["products_deleted"] = cur.rowcount
            cur = conn.execute("DELETE FROM assortments")
            stats["assortments_deleted"] = cur.rowcount
            cur = conn.execute("DELETE FROM districts")
            stats["districts_deleted"] = cur.rowcount
            cur = conn.execute("DELETE FROM cities")
            stats["cities_deleted"] = cur.rowcount
            conn.commit()
            return stats

    def _ensure_balance_row(self, conn, user_id: int) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO user_balances(user_id, balance) VALUES (?, 0)",
            (user_id,),
        )

    def get_user_balance(self, user_id: int) -> float:
        with self._connect() as conn:
            self._ensure_balance_row(conn, user_id)
            conn.commit()
            row = conn.execute(
                "SELECT balance FROM user_balances WHERE user_id = ?", (user_id,)
            ).fetchone()
            return float(row["balance"]) if row else 0.0

    def list_balance_topups(self, user_id: int, limit: int = 20) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, amount, created_at,
                       COALESCE(source, 'topup') AS source
                FROM balance_topups
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
            return self._rows_to_dicts(rows)

    def has_user_topup_activity(self, user_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM topup_requests
                WHERE user_id = ?
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            return row is not None

    def add_support_ticket(self, log_message_id: int, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO support_tickets(log_message_id, user_id)
                VALUES (?, ?)
                ON CONFLICT(log_message_id) DO UPDATE SET user_id = excluded.user_id
                """,
                (log_message_id, user_id),
            )
            conn.commit()

    def get_support_ticket_user(self, log_message_id: int) -> Optional[int]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT user_id FROM support_tickets
                WHERE log_message_id = ?
                """,
                (log_message_id,),
            ).fetchone()
            return int(row["user_id"]) if row else None

    def try_register_referral(self, invited_user_id: int, referrer_id: int) -> bool:
        if invited_user_id == referrer_id or referrer_id <= 0:
            return False
        with self._connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM referrals WHERE invited_user_id = ?",
                (invited_user_id,),
            ).fetchone()
            if exists:
                return False
            try:
                conn.execute(
                    """
                    INSERT INTO referrals(invited_user_id, referrer_id)
                    VALUES (?, ?)
                    """,
                    (invited_user_id, referrer_id),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def count_referrals(self, referrer_id: int) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM referrals WHERE referrer_id = ?",
                (referrer_id,),
            ).fetchone()
            return int(row["c"]) if row else 0

    def cancel_awaiting_topups_for_user(self, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE topup_requests SET status = 'cancelled'
                WHERE user_id = ? AND status = 'awaiting_claim'
                """,
                (user_id,),
            )
            conn.commit()

    def create_topup_request(
        self, user_id: int, amount: float, requisite_text: str
    ) -> int:
        with self._connect() as conn:
            self.cancel_awaiting_topups_for_user(user_id)
            cur = conn.execute(
                """
                INSERT INTO topup_requests(user_id, amount, requisite_text, status)
                VALUES (?, ?, ?, 'awaiting_claim')
                """,
                (user_id, amount, requisite_text.strip()),
            )
            conn.commit()
            return int(cur.lastrowid)

    def get_topup_request(self, request_id: int) -> Optional[Dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM topup_requests WHERE id = ?", (request_id,)
            ).fetchone()
            return dict(row) if row else None

    def submit_topup_claim(self, request_id: int, user_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE topup_requests SET status = 'pending_admin'
                WHERE id = ? AND user_id = ? AND status = 'awaiting_claim'
                """,
                (request_id, user_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def list_pending_topup_requests(self) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM topup_requests
                WHERE status = 'pending_admin'
                ORDER BY id ASC
                """
            ).fetchall()
            return self._rows_to_dicts(rows)

    def approve_topup_request(self, request_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, user_id, amount FROM topup_requests
                WHERE id = ? AND status = 'pending_admin'
                """,
                (request_id,),
            ).fetchone()
            if not row:
                return False
            uid = int(row["user_id"])
            amt = float(row["amount"])
            self._ensure_balance_row(conn, uid)
            conn.execute(
                "UPDATE user_balances SET balance = balance + ? WHERE user_id = ?",
                (amt, uid),
            )
            conn.execute(
                """
                INSERT INTO balance_topups(user_id, amount, source)
                VALUES (?, ?, 'topup')
                """,
                (uid, amt),
            )
            conn.execute(
                "UPDATE topup_requests SET status = 'completed' WHERE id = ?",
                (request_id,),
            )
            conn.commit()
            return True

    def reject_topup_request(self, request_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE topup_requests SET status = 'cancelled'
                WHERE id = ? AND status = 'pending_admin'
                """,
                (request_id,),
            )
            conn.commit()
            return cur.rowcount > 0

    def _migrate_captcha_column(self, conn) -> None:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(bot_users)")
        cols = [row[1] for row in cur.fetchall()]
        if "captcha_passed" not in cols:
            cur.execute("ALTER TABLE bot_users ADD COLUMN captcha_passed INTEGER NOT NULL DEFAULT 0")

    def is_captcha_passed(self, user_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT captcha_passed FROM bot_users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            return bool(row and int(row["captcha_passed"]))

    def pass_captcha(self, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE bot_users SET captcha_passed = 1 WHERE user_id = ?",
                (user_id,),
            )
            conn.commit()

    def upsert_bot_user(self, user_id: int, username: Optional[str] = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO bot_users(user_id, username, first_seen, last_seen)
                VALUES (?, ?, datetime('now'), datetime('now'))
                ON CONFLICT(user_id) DO UPDATE SET
                    username = COALESCE(excluded.username, bot_users.username),
                    last_seen = datetime('now')
                """,
                (user_id, username),
            )
            conn.commit()

    def count_bot_users(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM bot_users").fetchone()
            return int(row["c"]) if row else 0

    def admin_userbase_snapshot(self) -> Dict[str, int]:
        """Сводка для админки: кто в базе рассылки и рефералы."""
        with self._connect() as conn:

            def scalar(sql: str) -> int:
                row = conn.execute(sql).fetchone()
                if not row:
                    return 0
                v = row[0]
                return int(v) if v is not None else 0

            return {
                "bot_users": scalar("SELECT COUNT(*) FROM bot_users"),
                "referrals_total": scalar("SELECT COUNT(*) FROM referrals"),
                "referrers_distinct": scalar(
                    "SELECT COUNT(DISTINCT referrer_id) FROM referrals"
                ),
                "orders_users_distinct": scalar(
                    "SELECT COUNT(DISTINCT user_id) FROM orders"
                ),
                "balance_rows": scalar("SELECT COUNT(*) FROM user_balances"),
            }

    def list_bot_user_ids(self) -> List[int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT user_id FROM bot_users ORDER BY user_id ASC"
            ).fetchall()
            return [int(r["user_id"]) for r in rows]
