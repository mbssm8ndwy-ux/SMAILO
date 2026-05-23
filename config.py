import os

# Telegram bot token
API_TOKEN = os.environ.get("API_TOKEN", "8794787592:AAGmHgGBN-g7FnoJkcVKiAfJB1o2cSLC_5U")
PROXY_URL = os.environ.get("PROXY_URL", "socks5://127.0.0.1:12334")

# Comma-separated list of admin Telegram user IDs.
ADMIN_IDS = os.environ.get("ADMIN_IDS", "7998704133")
LOG_CHAT_ID = int(v) if (v := os.environ.get("LOG_CHAT_ID")) else -1003862800881
PAYMENT_NOTIFY_CHAT_ID = int(v) if (v := os.environ.get("PAYMENT_NOTIFY_CHAT_ID")) else -1003862800881
LOG_FORUM_TOPIC_ID = int(v) if (v := os.environ.get("LOG_FORUM_TOPIC_ID", "")).strip() else None
PAYMENT_NOTIFY_TOPIC_ID = int(v) if (v := os.environ.get("PAYMENT_NOTIFY_TOPIC_ID", "")).strip() else None

# Shop info
SUPPORT_CONTACT = os.environ.get("SUPPORT_CONTACT", "@qcryptopay")
PAYMENT_SUPPORT_CONTACT = os.environ.get("PAYMENT_SUPPORT_CONTACT", "@qcryptopay")
REVIEWS_TEXT = os.environ.get("REVIEWS_TEXT", "Канал с отзывами: добавьте ссылку в админке → Настройки → Отзывы.")
ABOUT_TEXT = os.environ.get("ABOUT_TEXT", "Добро пожаловать в магазин. Здесь вы можете оформить заказ в пару кликов.")
RULES_TEXT = os.environ.get("RULES_TEXT", "Перед заказом внимательно проверьте выбранный город, район и товар.")

# Резервные крипто-адреса
CRYPTO_USDT_TRC20_ADDRESS = os.environ.get("CRYPTO_USDT_TRC20_ADDRESS", "")
CRYPTO_BTC_ADDRESS = os.environ.get("CRYPTO_BTC_ADDRESS", "")
