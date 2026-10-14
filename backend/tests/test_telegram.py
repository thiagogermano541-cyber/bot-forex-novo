import httpx
import respx

from app.config import Settings
from app.db import Base, Delivery, Session, engine
from app.telegram import deliver, enqueue


@respx.mock
async def test_delivery_dedup_and_authorized_chat():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    cfg = Settings(telegram_enabled=True, telegram_bot_token="TEST_ONLY", telegram_chat_ids="12345")
    route = respx.post("https://api.telegram.org/botTEST_ONLY/sendMessage").mock(return_value=httpx.Response(200, json={"ok": True}))
    async with Session() as session:
        await enqueue(session, cfg, "test-event", "Mensagem sem segredos")
        await session.commit()
        await enqueue(session, cfg, "test-event", "Mensagem repetida")
        await session.commit()
        async with httpx.AsyncClient() as client:
            await deliver(session, client, cfg)
    assert route.call_count == 1
    assert route.calls[0].request.read().find(b"12345") >= 0
