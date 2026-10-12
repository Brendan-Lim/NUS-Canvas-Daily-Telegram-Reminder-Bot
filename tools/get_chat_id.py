"""Local-only helper. Send /start to YOUR Telegram bot first, then run this script."""
import os
import sys
from getpass import getpass
import requests

bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "") or getpass("Paste your Telegram bot token (hidden): ").strip()
if not bot_token:
    sys.exit("Telegram bot token is required")
try:
    response = requests.get(
        f"https://api.telegram.org/bot{bot_token}/getUpdates",
        timeout=(10, 30), allow_redirects=False,
    )
    if response.status_code != 200:
        sys.exit("Could not fetch Telegram updates. Check your bot token and ensure the bot isn't using a webhook.")
    payload = response.json()
except (requests.RequestException, ValueError):
    sys.exit("Telegram could not be reached")
if not payload.get("ok"):
    sys.exit("Telegram rejected the request")
ids = sorted({
    str(c["id"])
    for update in payload.get("result", [])
    for obj in (update.get("message"), update.get("edited_message"))
    if isinstance(obj, dict)
    for c in (obj.get("chat"),)
    if isinstance(c, dict) and c.get("type") == "private" and isinstance(c.get("id"), int)
})
if ids:
    print("Private chat ID(s): " + ", ".join(ids))
else:
    print("No private chat IDs found. Open your bot in Telegram, send /start, then retry.")
