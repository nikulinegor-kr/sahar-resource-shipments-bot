# app.py
import os
import html
import requests
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional

app = FastAPI(title="Snab Notify", version="1.0.0")

# --- ENV ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")  # e.g. -1003141855190
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# --- MODELS ---
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

# --- HELPERS ---
def tg_send_html(text: str) -> bool:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    r = requests.post(url, json=data, timeout=15)
    return r.ok

def render_message(p: NotifyPayload) -> str:
    # безопасим текст
    esc = lambda s: html.escape(s or "")
    parts = []
    if p.order_id: parts.append(f"<b>Заявка:</b> {esc(p.order_id)}")
    if p.status:   parts.append(f"<b>Статус:</b> {esc(p.status)}")
    if p.ship_date: parts.append(f"<b>Дата отгрузки:</b> {esc(p.ship_date)}")

    # Комментарий (в нём ты сейчас дублируешь: Заявка, Статус, ТК, №ТТН, даты)
    if p.comment:
        parts.append(f"<b>Комментарий:</b> {esc(p.comment)}")

    # Ответственный
    if p.responsible:
        r = p.responsible
        if r.username:
            parts.append(f"<b>Ответственный:</b> @{esc(r.username)}")
        elif r.user_id:
            parts.append(f"<b>Ответственный:</b> tg://user?id={r.user_id}")
        elif r.name:
            parts.append(f"<b>Ответственный:</b> {esc(r.name)}")

    return "\n".join(parts)

# --- ROUTES ---
@app.get("/health")
def health():
    return {"ok": True}

@app.post("/notify")
def notify(payload: NotifyPayload, authorization: str = Header(default="")):
    if WEBHOOK_SECRET and authorization != f"Bearer {WEBHOOK_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not BOT_TOKEN or not CHAT_ID:
        raise HTTPException(status_code=500, detail="Telegram not configured")

    msg = render_message(payload)
    ok = tg_send_html(msg)
    if not ok:
        raise HTTPException(status_code=502, detail="Telegram send failed")
    return {"ok": True}
