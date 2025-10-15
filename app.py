# app.py
import os
import html
import requests
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, Request, Header, HTTPException

app = FastAPI(title="Snab Bot & Notify", version="1.0.0")

# --- ENV ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()              # дефолтная группа для уведомлений
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
SHEET_CSV_URL = os.getenv("SHEET_CSV_URL", "").strip()  # опционально

# --- helpers ---
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

def tg_send_message(chat_id: str | int, text: str, parse_mode: str = "HTML") -> Dict[str, Any]:
    if not BOT_TOKEN:
        return {"ok": False, "error": "BOT_TOKEN is empty"}
    url = f"{TG_API}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    r = requests.post(url, json=data, timeout=15)
    try:
        return r.json()
    except Exception:
        return {"ok": False, "status": r.status_code, "text": r.text}

def esc(s: Optional[str]) -> str:
    return html.escape((s or "").strip())


# =========================
#   СЕРВИСНЫЕ ЭНДПОИНТЫ
# =========================

@app.get("/health")
def health():
    return {"ok": True, "service": "snab-bot", "webhook": "/tg"}

@app.get("/tg")
def tg_get_probe():
    # просто быстрый ответ, чтобы ты мог проверить, что маршрут существует
    return {"ok": True, "route": "/tg"}


# =========================
#   TELEGRAM WEBHOOK
# =========================
@app.post("/tg")
async def tg_webhook(req: Request):
    """
    Универсальный хэндлер Telegram.
    Обрабатывает /start, /help, /id. Любой другой текст пока игнорируется (но эндпоинт отвечает 200).
    """
    if not BOT_TOKEN:
        # нет токена — нет смысла принимать апдейты
        raise HTTPException(status_code=500, detail="BOT_TOKEN not configured")

    try:
        update = await req.json()
    except Exception:
        update = {}

    # Разбор источника сообщения
    message = update.get("message") or update.get("channel_post")
    if not message:
        # апдейт не содержит текста (edited_message и т.п.) — просто подтверждаем
        return {"ok": True, "skipped": True}

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()

    # Команды
    if text.startswith("/start"):
        tg_send_message(chat_id, (
            "👋 <b>Бот снабжения</b> готов.\n\n"
            "Доступные команды:\n"
            "• /help — список команд\n"
            "• /id — ваш Telegram ID"
        ))
    elif text.startswith("/help"):
        tg_send_message(chat_id, (
            "<b>Команды:</b>\n"
            "• /start — начать работу с ботом\n"
            "• /help — список команд\n"
            "• /id — показать ваш Telegram ID\n\n"
            "Бот также принимает внешние уведомления на /notify."
        ))
    elif text.startswith("/id"):
        user = message.get("from", {})
        uid = user.get("id")
        uname = user.get("username")
        tg_send_message(chat_id, f"🆔 Ваш ID: <code>{uid}</code>\n👤 @{esc(uname) if uname else '—'}")
    else:
        # Для любого другого текста — молчим, но webhook подтверждаем
        pass

    return {"ok": True}


# =========================
#   УВЕДОМЛЕНИЯ ИЗ ТАБЛИЦ
# =========================
# Модели совместимы с твоим текущим сценариям Google Apps Script
from pydantic import BaseModel, Field

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
    arrival_date: Optional[str] = None
    carrier: Optional[str] = None
    ttn: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    applicant: Optional[str] = None
    comment: Optional[str] = None
    responsible: Optional[Responsible] = None
    items: List[Item] = []

def render_notify_message(p: NotifyPayload) -> str:
    parts: List[str] = ["📦 <b>Уведомление о заявке</b>"]
    if p.order_id:     parts.append(f"\n🧾 <b>Заявка:</b> {esc(p.order_id)}")
    if p.priority:     parts.append(f"\n⭐ <b>Приоритет:</b> {esc(p.priority)}")
    if p.status:       parts.append(f"\n🚚 <b>Статус:</b> {esc(p.status)}")
    if p.ship_date:    parts.append(f"\n📅 <b>Дата отгрузки:</b> {esc(p.ship_date)}")
    if p.arrival_date: parts.append(f"\n📦 <b>Дата прибытия:</b> {esc(p.arrival_date)}")
    if p.carrier:      parts.append(f"\n🚛 <b>ТК:</b> {esc(p.carrier)}")
    if p.ttn:          parts.append(f"\n📄 <b>№ ТТН:</b> {esc(p.ttn)}")
    if p.applicant:    parts.append(f"\n👤 <b>Заявитель:</b> {esc(p.applicant)}")

    # Ответственный (если есть)
    if p.responsible:
        r = p.responsible
        if r.username:
            parts.append(f"\n👤 <b>Ответственный:</b> @{esc(r.username)}")
        elif r.user_id:
            parts.append(f"\n👤 <b>Ответственный:</b> tg://user?id={r.user_id}")
        elif r.name:
            parts.append(f"\n👤 <b>Ответственный:</b> {esc(r.name)}")

    return "".join(parts)


@app.post("/notify")
async def notify(payload: NotifyPayload, authorization: str = Header(default="")):
    if WEBHOOK_SECRET and authorization != f"Bearer {WEBHOOK_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not CHAT_ID:
        raise HTTPException(status_code=500, detail="CHAT_ID is empty")

    msg = render_notify_message(payload)
    res = tg_send_message(CHAT_ID, msg)
    if not res.get("ok"):
        raise HTTPException(status_code=502, detail=f"Telegram error: {res}")

    return {"ok": True, "sent_to": CHAT_ID}
