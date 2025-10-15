# app.py
import os
import html
import requests
from typing import Optional, List

from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel

# =======================
#   Конфиг из окружения
# =======================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()  # можно оставить пустым — тогда уведомления из /notify не шлём
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
SHEET_CSV_URL = os.getenv("SHEET_CSV_URL", "").strip()  # на будущее

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

app = FastAPI(title="BotSnab / Snab Notify", version="1.2.0")


# =======================
#   Вспомогательные
# =======================
def tg_send(chat_id: int | str, text: str, parse_mode: str = "HTML") -> bool:
    """
    Отправка сообщения в Telegram.
    """
    try:
        r = requests.post(
            f"{TG_API}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        print("=== Telegram API response ===")
        print("Status:", r.status_code)
        print("Body:", r.text)
        return r.ok
    except Exception as e:
        print("TG send error:", e)
        return False


# =======================
#   Модели уведомления
# =======================
class Responsible(BaseModel):
    name: Optional[str] = None
    username: Optional[str] = None  # без @
    user_id: Optional[int] = None


class Item(BaseModel):
    name: str
    qty: Optional[float] = None
    unit: Optional[str] = None


class NotifyPayload(BaseModel):
    order_id: str
    recipient: str
    city: Optional[str] = None
    phone: Optional[str] = None
    ship_date: Optional[str] = None
    status: Optional[str] = None
    comment: Optional[str] = None
    responsible: Optional[Responsible] = None
    items: List[Item] = []


def render_message(p: NotifyPayload) -> str:
    esc = lambda s: html.escape(s or "")
    parts = ["<b>📦 Уведомление о заявке</b>"]

    if p.order_id:
        parts.append(f"🧾 <b>Заявка:</b> {esc(p.order_id)}")
    if p.status:
        parts.append(f"🚚 <b>Статус:</b> {esc(p.status)}")
    if p.ship_date:
        parts.append(f"📅 <b>Дата отгрузки:</b> {esc(p.ship_date)}")
    if p.comment:
        parts.append(f"📝 <b>Комментарий:</b> {esc(p.comment)}")

    # Заявитель/Ответственный — по правилам: username > user_id > name
    if p.responsible:
        r = p.responsible
        responsible_str = ""
        if r and r.username:
            responsible_str = f"@{esc(r.username)}"
        elif r and r.user_id:
            responsible_str = f"tg://user?id={r.user_id}"
        elif r and r.name:
            responsible_str = esc(r.name)

        if responsible_str:
            parts.append(f"👤 <b>Заявитель:</b> {responsible_str}")

    return "\n".join(parts)


# =======================
#   Служебные ручки
# =======================
@app.get("/health")
def health():
    return {"ok": True}


@app.get("/")
def root():
    return {"ok": True, "service": "BotSnab"}


# =======================
#   Отправка из Google-таблиц (/notify)
# =======================
@app.post("/notify")
def notify(payload: NotifyPayload, authorization: str = ""):
    """
    Ожидает заголовок Authorization: Bearer <WEBHOOK_SECRET>
    и шлёт сообщение в CHAT_ID (если задан).
    """
    if WEBHOOK_SECRET and authorization != f"Bearer {WEBHOOK_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not CHAT_ID:
        raise HTTPException(status_code=400, detail="CHAT_ID is not set")

    msg = render_message(payload)
    ok = tg_send(CHAT_ID, msg)

    if not ok:
        raise HTTPException(status_code=502, detail="Telegram send failed")

    return {"ok": True}


# =======================
#   Telegram Webhook (/tg)
# =======================
@app.post("/tg")
async def tg_webhook(update: dict):
    """
    Принимает апдейты Telegram.
    Важно: всегда быстро возвращаем 200 ({"ok": True}),
    иначе Telegram будет считать, что вебхук не работает.
    """
    try:
        # Для групп/каналов апдейт может прийти как message или channel_post
        msg = update.get("message") or update.get("channel_post")
        if not msg:
            # Сервисные события (my_chat_member и т.п.) просто подтверждаем
            return {"ok": True}

        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip()

        # --- простые команды ---
        if text.startswith("/start"):
            tg_send(
                chat_id,
                "👋 Привет! Я <b>BotSnab</b> — бот снабжения.\n"
                "Доступные команды:\n"
                "/help — список команд\n"
                "/id — показать ваш Telegram ID\n",
            )

        elif text.startswith("/help"):
            tg_send(
                chat_id,
                "📖 <b>Команды</b>:\n"
                "/start — начать работу\n"
                "/help — список команд\n"
                "/id — ваш Telegram ID\n"
                "/today — отгрузки сегодня (скоро)\n"
                "/week — отгрузки на неделе (скоро)\n"
                "/status &lt;Статус&gt; — заявки по статусу (скоро)\n",
            )

        elif text.startswith("/id"):
            uid = msg.get("from", {}).get("id")
            tg_send(chat_id, f"🆔 Ваш Telegram ID: <code>{uid}</code>")

        # здесь позже можно добавить: /today, /week, /status и т.п.
        # обработчики должны быть быстрыми (или вызывать фоновые задачи).

    except Exception as e:
        # Логируем, но всегда отвечаем 200 OK, чтобы Telegram не «ругался»
        print("tg_webhook error:", e)

    return {"ok": True}


# Локальный запуск (на Koyeb обычно не нужен — uvicorn стартует из Dockerfile / Procfile)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000)
