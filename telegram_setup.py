"""One-time Telegram setup.

1. In Telegram, open @BotFather, send /newbot, follow the steps, copy the bot token.
2. Put it in .env as  TELEGRAM_BOT_TOKEN=...
3. Open your new bot in Telegram and send it any message (for example: hi).
4. Run:  python telegram_setup.py
It finds your chat id, saves it to .env and sends a test message.
"""
import os
import sys

import requests


def main():
    env = {}
    for line in open(".env"):
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.strip().split("=", 1)
            env[k.strip()] = v.strip().strip('"')
    tok = env.get("TELEGRAM_BOT_TOKEN")
    if not tok:
        sys.exit("Add TELEGRAM_BOT_TOKEN=... to .env first.")
    r = requests.get(f"https://api.telegram.org/bot{tok}/getUpdates", timeout=20).json()
    if not r.get("ok"):
        sys.exit(f"Telegram rejected the token: {r.get('description')}")
    chats = [u["message"]["chat"]["id"] for u in r["result"] if "message" in u]
    if not chats:
        sys.exit("No message found. Open your bot in Telegram, send it 'hi', then run this again.")
    chat = str(chats[-1])
    if env.get("TELEGRAM_CHAT_ID") != chat:
        lines = [l for l in open(".env").read().splitlines() if not l.startswith("TELEGRAM_CHAT_ID=")]
        open(".env", "w").write("\n".join(lines + [f"TELEGRAM_CHAT_ID={chat}"]) + "\n")
    s = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": chat, "text": "NIFTY signal system: Telegram is connected."}, timeout=20).json()
    print("Telegram connected. Test message sent." if s.get("ok") else f"Send failed: {s.get('description')}")


if __name__ == "__main__":
    main()
