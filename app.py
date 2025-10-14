import os, html, requests
from datetime import datetime
from typing import Optional, List
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Snab Notify", version="1.1.0")

# === КОНФИГ ===
BOT_TOKEN = os.getenv("BOT_TOKEN", "8436347589:AAGAcEgto8ebT4sd6_4gBy5EJ4NL9hKa_Rg")
CHAT_ID = os.getenv("CHAT_ID", "-1003141855190")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "sahar2025secure_longtoken")

# === МОДЕЛИ ===
class Responsible(BaseModel):
    name: Optional[str] = None
    username: Optional[str] = None
    user_id: Optional[int] = None

class Item(BaseModel):
    name: str
    qty: Optional[float] = None
    unit: Optional[str] = None

class NotifyPayload(BaseModel):
    order_id: Optional[str] = Field(None, description="Номер заявки")
    priority: Optional[str] = None
    status: Optional[str] = None
    ship_date: Optional[str] = None
    arrival: Optional[str] = None
    carrier: Optional[str] = None
    ttn: Optional[str] = None
    applicant: Optional[str] = None
    recipient: Optional[str] = None
    items: List[Item] = []

# === ОТПРАВКА В TELEGRAM ===
def tg_send_html(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    r = requests.post(url, json=data, timeout=15)
    print("=== Telegram API response ===")
    print("Status:", r.status_code)
    print("Body:", r.text)
    return r.ok, r.status_code, r.text

# === ФОРМИРОВАНИЕ СООБЩЕНИЯ ===
def render_message(p: NotifyPayload) -> str:
    esc = lambda s: html.escape(s or "")
    parts = ["📦 <b>Уведомление о заявке</b>", ""]

    if p.order_id:
        parts.append(f"🧾 <b>Заявка:</b> {esc(p.order_id)}")
    if p.priority:
        parts.append(f"⭐ <b>Приоритет:</b> {esc(p.priority)}")
    if p.status:
        parts.append(f"🚚 <b>Статус:</b> {esc(p.status)}")
    if p.ship_date:
        parts.append(f"📅 <b>Дата отгрузки:</b> {esc(p.ship_date)}")
    if p.arrival:
        parts.append(f"📦 <b>Дата прибытия:</b> {esc(p.arrival)}")
    if p.carrier:
        parts.append(f"🚛 <b>ТК:</b> {esc(p.carrier)}")
    if p.ttn:
        parts.append(f"📄 <b>№ ТТН:</b> {esc(p.ttn)}")
    if p.applicant:
        parts.append(f"👤 <b>Заявитель:</b> {esc(p.applicant)}")

    # Добавляем дату и время отправки
    ts = datetime.now().strftime("%d.%m.%Y, %H:%M")
    parts.append("")
    parts.append(f"🕒 <i>Отправлено: {ts}</i>")

    return "\n".join(parts)

# === API ===
@app.get("/health")
def health():
    return {"ok": True, "time": datetime.now().isoformat()}

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

    return {"ok": True, "sent": True, "status_code": sc}
