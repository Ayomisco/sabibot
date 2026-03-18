"""Get your Telegram Chat ID by sending /start to the bot first, then run this."""
import httpx
import os
from dotenv import load_dotenv

load_dotenv()

token = os.getenv("TELEGRAM_BOT_TOKEN", "")
if not token:
    print("ERROR: TELEGRAM_BOT_TOKEN not set in .env")
    exit(1)

resp = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates")
data = resp.json()

if not data.get("ok"):
    print(f"Telegram API error: {data}")
    exit(1)

updates = data.get("result", [])
if not updates:
    print("No messages found!")
    print("1. Open Telegram and search for your bot: @Sabi01_bot")
    print("2. Send /start to the bot")
    print("3. Run this script again")
    exit(0)

# Find unique chat IDs
chats = {}
for update in updates:
    msg = update.get("message", {})
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    if chat_id:
        name = chat.get("first_name", "") + " " + chat.get("last_name", "")
        username = chat.get("username", "")
        chats[chat_id] = f"{name.strip()} (@{username})" if username else name.strip()

print("\n=== Your Telegram Chat ID(s) ===")
for cid, name in chats.items():
    print(f"  Chat ID: {cid}  ({name})")
print(f"\nAdd this to your .env file:")
print(f"  TELEGRAM_CHAT_ID={list(chats.keys())[0]}")
