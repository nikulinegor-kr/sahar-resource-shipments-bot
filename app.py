# app.py
import os
import html
from datetime import datetime
from typing import Optional, List

import requests
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Snab Notify", version="1.3.0")

# === ENV (задай в Koyeb → Settings → Environment variables) ===
# BOT_TOKEN, CHAT_ID, WEBHOOK_SECRET
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# === MODELS ===
class Responsible(BaseModel):
    name: Optional[str] = None
    username: Optional[str] = None   # без @
    user_id: Optional[int] = None

class Item(BaseModel):
    name: str
    qty: Optional[float] = None
    unit: Optional[str] = None

class NotifyPayload(BaseModel):
    # обязательные
    order_id: str
    recipient: str

    # опциональные
    priority: Optional[str] = None             # ← НОВОЕ: Приоритет
    city: Optional[str] = None
    phone: Optional[str] = None
    ship_date: Optional[str] = None            # YYYY-MM-DD
    arrival_date: Optional[str] = None         # YYYY-MM-DD
    status: Optional[str] = None
    carrier: Optional[str] = None              # ТК
    ttn: Optional[str] = None                  # № ТТН
    comment: Optional[str] = None
    responsible: Optional[Responsible] = None
    items: List[Item] = Field(default_factory=list)

# === HELPERS ===
RU_MONTHS = [
    "января","февраля","марта","апреля","мая","июня",
    "июля","августа","сентября","октября","ноября","декабря"
]

def fmt_pretty_date(date_str: Optional[str]) -> str:
    """YYYY-MM-DD → '13 октября 2025'. Если не распарсилось — вернём как есть."""
    if not date_str:
        return ""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{d.day} {RU_MONTHS[d.month-1]} {d.year}"
    except Exception:
        return date_str

def tg_send_html(text: str, chat_id: Optional[str] = None):
    """Отправка сообщения в Telegram (HTML)."""
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("BOT_TOKEN/CHAT_ID не заданы в переменных окружения")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": chat_id or CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    r = requests.post(url, json=data, timeout=15)

    # лог для диагностики
    print("=== Telegram API response ===")
    print("Status:", r.status_code)
    print("Body:", r.text)

    return r.ok, r.status_code, r.text

def render_message(p: NotifyPayload) -> str:
    """Собирает красивую карточку уведомления."""
    esc = lambda s: html.escape(s or "")
    parts = []
    parts.append("📦 <b>Уведомление о заявке</b>\n")

    if p.order_id:
        parts.append(f"🧾 <b>Заявка:</b> {esc(p.order_id)}")
    if p.priority:  # ← Приоритет сразу после Заявка
        parts.append(f"⭐ <b>Приоритет:</b> {esc(p.priority)}")
    if p.status:
        parts.append(f"🚚 <b>Статус:</b> {esc(p.status)}")
    if p.ship_date:
        parts.append(f"📅 <b>Дата отгрузки:</b> {fmt_pretty_date(p.ship_date)}")
    if p.arrival_date:
        parts.append(f"📦 <b>Дата прибытия:</b> {fmt_pretty_date(p.arrival_date)}")
    if p.carrier:
        parts.append(f"🚛 <b>ТК:</b> {esc(p.carrier)}")
    if p.ttn:
        parts.append(f"📄 <b>№ ТТН:</b> {esc(p.ttn)}")
    if p.responsible:
        r = p.responsible
        if r.username:
            parts.append(f"👤 <b>Ответственный:</b> @{esc(r.username)}")
        elif r.user_id:
            parts.append(f"👤 <b>Ответственный:</b> tg://user?id={r.user_id}")
        elif r.name:
            parts.append(f"👤 <b>Ответственный:</b> {esc(r.name)}")

    return "\n".join(parts)

# === ROUTES ===
@app.get("/health")
def health():
    return {"ok": True}

@app.post("/notify")
def notify(payload: NotifyPayload, authorization: str = Header(default="")):
    # Проверка секрета
    if WEBHOOK_SECRET and authorization != f"Bearer {WEBHOOK_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    msg = render_message(payload)
    ok, sc, txt = tg_send_html(msg)

    if not ok:
        print(f"Telegram error {sc}: {txt}")
        raise HTTPException(status_code=502, detail=f"Telegram error {sc}: {txt}")

    return {"ok": True}
