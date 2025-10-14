# app.py
import os, html, requests
from typing import Optional, List
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Snab Notify", version="1.0.0")

BOT_TOKEN = os.getenv("8268920782:AAFcxeoa7ny8cKfMzuP84r2iVe22Q7aHhyE", "")
CHAT_ID = os.getenv("-1003141855190", "")            # -1003141855190
WEBHOOK_SECRET = os.getenv("sahar2025secure_longtoken", "")

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

def tg_send_html(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=15)
    return r.ok

def render_message(p: NotifyPayload) -> str:
    esc = lambda s: html.escape(s or "")
    parts = []
    if p.order_id:   parts.append(f"<b>Заявка:</b> {esc(p.order_id)}")
    if p.status:     parts.append(f"<b>Статус:</b> {esc(p.status)}")
    if p.ship_date:  parts.append(f"<b>Дата отгрузки:</b> {esc(p.ship_date)}")
    if p.comment:    parts.append(f"<b>Комментарий:</b> {esc(p.comment)}")
    if p.responsible:
        r = p.responsible
        if r.username: parts.append(f"<b>Ответственный:</b> @{esc(r.username)}")
        elif r.user_id: parts.append(f"<b>Ответственный:</b> tg://user?id={r.user_id}")
        elif r.name:   parts.append(f"<b>Ответственный:</b> {esc(r.name)}")
    return "\n".join(parts)

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/notify")
def notify(payload: NotifyPayload, authorization: str = Header(default="")):
    if WEBHOOK_SECRET and authorization != f"Bearer {WEBHOOK_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")
    msg = render_message(payload)
    if not tg_send_html(msg):
        raise HTTPException(status_code=502, detail="Telegram send failed")
    return {"ok": True}
