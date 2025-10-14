from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import requests
import os
import html

app = FastAPI(title="SnabNotifyBot API")

# === ENVIRONMENT ===
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")


# === MODELS ===
class Responsible(BaseModel):
    name: Optional[str] = None
    username: Optional[str] = None
    user_id: Optional[int] = None


class Item(BaseModel):
    name: str
    qty: Optional[float] = None
    unit: Optional[str] = None


class NotifyPayload(BaseModel):
    order_id: str
    recipient: str
    ship_date: Optional[str] = None
    status: Optional[str] = None
    comment: Optional[str] = None
    responsible: Optional[Responsible] = None
    city: Optional[str] = None
    phone: Optional[str] = None
    items: List[Item] = []


# === HELPERS ===
def escape(text: Optional[str]) -> str:
    return html.escape(text or "")


def send_message(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    r = requests.post(url, json=payload)
    return r.ok


def format_message(data: NotifyPayload) -> str:
    lines = []
    if data.order_id:
        lines.append(f"<b>Заявка:</b> {escape(data.order_id)}")
    if data.status:
        lines.append(f"<b>Статус:</b> {escape(data.status)}")
    if data.ship_date:
        lines.append(f"<b>Дата отгрузки:</b> {escape(data.ship_date)}")
    if data.comment:
        lines.append(f"<b>Комментарий:</b> {escape(data.comment)}")

    if data.responsible:
        r = data.responsible
        if r.username:
            lines.append(f"<b>Ответственный:</b> @{escape(r.username)}")
        elif r.user_id:
            lines.append(f"<b>Ответственный:</b> tg://user?id={r.user_id}")
        elif r.name:
            lines.append(f"<b>Ответственный:</b> {escape(r.name)}")
    return "\n".join(lines)


# === ROUTES ===
@app.get("/health")
def health():
    return {"ok": True}


@app.post("/notify")
def notify(payload: NotifyPayload, authorization: str = Header(None)):
    if WEBHOOK_SECRET and authorization != f"Bearer {WEBHOOK_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    msg = format_message(payload)
    if not send_message(msg):
        raise HTTPException(status_code=500, detail="Failed to send to Telegram")
    return {"ok": True}
