# app.py
import os
import html
import csv
import io
import json
import logging
from typing import Optional, List, Dict, Any

import requests
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")

app = FastAPI(title="Snab Notify + Bot", version="2.0.0")

# ----- ENV -----
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()  # можно не использовать в вебхуке
SHEET_CSV_URL = os.getenv("SHEET_CSV_URL", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else None


# ---------- МОДЕЛИ ДЛЯ /notify ----------
class Responsible(BaseModel):
    name: Optional[str] = None
    username: Optional[str] = None  # без @
    user_id: Optional[int] = None


class Item(BaseModel):
    name: str
    qty: Optional[float] = None
    unit: Optional[str] = None


class NotifyPayload(BaseModel):
    order_id: str = Field(..., description="Номер заявки/заказа")
    recipient: str = Field(..., description="Получатель (компания)")
    city: Optional[str] = None
    phone: Optional[str] = None
    ship_date: Optional[str] = None
    status: Optional[str] = None
    comment: Optional[str] = None
    responsible: Optional[Responsible] = None
    items: List[Item] = []


# ---------- СЕРВИСНЫЕ ----------
def tg_send_message(chat_id: str, text: str, parse_mode: Optional[str] = "HTML"):
    if not TG_API:
        log.error("BOT_TOKEN is empty; cannot call Telegram")
        return False, 500, "BOT_TOKEN is empty"
    url = f"{TG_API}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    r = requests.post(url, json=data, timeout=15)
    log.info("TG send --> %s %s", r.status_code, r.text[:500])
    return r.ok, r.status_code, r.text


def escape(s: Optional[str]) -> str:
    return html.escape(s or "")


def render_notify_message(p: NotifyPayload) -> str:
    parts = []
    if p.order_id:
        parts.append(f"🧾 <b>Заявка:</b> {escape(p.order_id)}")
    if p.status:
        parts.append(f"🚚 <b>Статус:</b> {escape(p.status)}")
    if p.ship_date:
        parts.append(f"📅 <b>Дата отгрузки:</b> {escape(p.ship_date)}")
    if p.comment:
        parts.append(f"📝 <b>Комментарий:</b> {escape(p.comment)}")
    if p.responsible:
        r = p.responsible
        if r.username:
            parts.append(f"👤 <b>Заявитель:</b> @{escape(r.username)}")
        elif r.user_id:
            parts.append(f"👤 <b>Заявитель:</b> tg://user?id={r.user_id}")
        elif r.name:
            parts.append(f"👤 <b>Заявитель:</b> {escape(r.name)}")
    return "📦 <b>Уведомление о заявке</b>\n\n" + "\n".join(parts)


def load_sheet_rows() -> List[Dict[str, str]]:
    """Опционально: читает опубликованный CSV (если указан SHEET_CSV_URL)."""
    if not SHEET_CSV_URL:
        return []
    r = requests.get(SHEET_CSV_URL, timeout=20)
    r.raise_for_status()
    content = r.content.decode("utf-8", errors="ignore")
    reader = csv.DictReader(io.StringIO(content))
    return [dict(row) for row in reader]


# ---------- ПУТИ ДЛЯ ПРОВЕРОК ----------
@app.get("/health")
def health():
    return {"ok": True}


@app.get("/tg")
def tg_get_probe():
    # простой GET, чтобы Telegram и вы могли увидеть, что маршрут жив
    return {"ok": True, "route": "/tg"}


# ---------- ВЕБХУК TELEGRAM ----------
@app.post("/tg")
async def tg_webhook(req: Request):
    """
    Основной вебхук Telegram. Обрабатывает /start, /help, /id.
    Включите "Group Privacy: DISABLED" у бота, чтобы видеть сообщения в группе.
    """
    if not TG_API:
        raise HTTPException(status_code=500, detail="BOT_TOKEN is not set")

    try:
        update: Dict[str, Any] = await req.json()
    except Exception:
        body = await req.body()
        log.error("Bad JSON from Telegram: %r", body[:500])
        raise HTTPException(status_code=400, detail="Bad JSON")

    log.info("TG update: %s", json.dumps(update)[:2000])

    # Определяем сообщение и чат
    msg = update.get("message") or update.get("channel_post")
    if not msg:
        # для my_chat_member/прочих апдейтов просто 200 OK
        return {"ok": True}

    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()
    from_user = msg.get("from") or {}
    user_id = from_user.get("id")
    username = from_user.get("username") or ""
    first_name = from_user.get("first_name") or ""
    last_name = from_user.get("last_name") or ""
    full_name = (" ".join([first_name, last_name])).strip() or username or str(user_id)

    # Разбираем команды только если они действительно есть
    # Telegram добавляет entities.type == "bot_command"
    entities = msg.get("entities") or []
    is_command = any(e.get("type") == "bot_command" for e in entities)

    # Функции ответов
    def reply(text_: str):
        tg_send_message(str(chat_id), text_)

    # Команды
    if is_command:
        cmd = text.split()[0].lower()
        if cmd.startswith("/start"):
            reply(
                "👋 Привет! Я <b>BotSnab</b> — бот снабжения.\n"
                "Доступные команды:\n"
                "• /help — список команд\n"
                "• /id — показать ваш Telegram ID\n"
            )
            return {"ok": True}

        if cmd.startswith("/help"):
            reply(
                "<b>Доступные команды</b>:\n"
                "• /start — начать работу\n"
                "• /help — список команд\n"
                "• /id — показать ваш Telegram ID\n"
            )
            return {"ok": True}

        if cmd.startswith("/id"):
            reply(f"🪪 Ваш ID: <code>{user_id}</code>\nИмя: <b>{escape(full_name)}</b>")
            return {"ok": True}

        # Неизвестная команда
        reply("❓ Неизвестная команда. Напишите /help")
        return {"ok": True}

    # Если не команда — молча OK (чтобы не спамить в группах)
    return {"ok": True}


# ---------- ВАШ СТАРЫЙ ВХОД ДЛЯ УВЕДОМЛЕНИЙ ИЗ ТАБЛИЦ ----------
@app.post("/notify")
def notify(payload: NotifyPayload, authorization: str = Header(default="")):
    # Авторизация по Bearer
    if WEBHOOK_SECRET and authorization != f"Bearer {WEBHOOK_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Куда отправлять: если пришёл chat_id в ENV — используем его, иначе сообщение не шлём
    if not CHAT_ID:
        raise HTTPException(status_code=500, detail="CHAT_ID not configured")

    msg = render_notify_message(payload)
    ok, sc, txt = tg_send_message(CHAT_ID, msg)

    if not ok:
        log.error("Telegram error %s: %s", sc, txt)
        raise HTTPException(status_code=502, detail=f"Telegram error {sc}: {txt}")

    return {"ok": True}
