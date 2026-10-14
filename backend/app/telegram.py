from sqlalchemy import select

from .db import Delivery
from .schemas import utcnow


def allowed_chats(config):
    return [chat.strip() for chat in config.telegram_chat_ids.split(",") if chat.strip().lstrip("-").isdigit()]


async def enqueue(session, config, event_key, message):
    if not config.telegram_enabled or not config.telegram_bot_token.get_secret_value():
        return
    for chat in allowed_chats(config):
        existing = await session.scalar(select(Delivery).where(Delivery.event_key == event_key, Delivery.chat_id == chat))
        if not existing:
            session.add(Delivery(event_key=event_key, chat_id=chat, text=message[:4000]))


async def deliver(session, client, config):
    from datetime import timedelta
    if not config.telegram_enabled or not config.telegram_bot_token.get_secret_value():
        return
    rows = (await session.scalars(select(Delivery).where(Delivery.state == "PENDING", Delivery.next_attempt <= utcnow()).limit(30).with_for_update(skip_locked=True))).all()
    for row in rows:
        row.attempts += 1
        if row.chat_id not in allowed_chats(config):
            row.state, row.error = "FAILED", "CHAT_NOT_AUTHORIZED"
            continue
        try:
            response = await client.post(f"https://api.telegram.org/bot{config.telegram_bot_token.get_secret_value()}/sendMessage",
                                         json={"chat_id": row.chat_id, "text": row.text, "protect_content": True})
            data = response.json()
            if response.is_success and data.get("ok"):
                row.state, row.error = "DELIVERED", None
            else:
                row.error = f"TELEGRAM_HTTP_{response.status_code}"
                if response.status_code in (400, 401, 403) or row.attempts >= 5:
                    row.state = "FAILED"
                else:
                    delay = min(data.get("parameters", {}).get("retry_after", 2 ** row.attempts), 3600)
                    row.next_attempt = utcnow() + timedelta(seconds=delay)
        except Exception:
            # Never log the URL: it contains the bot token.
            row.error = "TELEGRAM_TRANSPORT_ERROR"
            row.state = "FAILED" if row.attempts >= 5 else "PENDING"
            row.next_attempt = utcnow() + timedelta(seconds=2 ** row.attempts)
    await session.commit()
