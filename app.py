from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
import os
import requests

app = FastAPI()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID", "-1003141855190")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

class Item(BaseModel):
    name: str
    qty: int
    unit: str

class Responsible(BaseModel):
    name: str
    username: str | None = None

class Shipment(BaseModel):
    order_id: str
    recipient: str
    city: str
    phone: str
    items: list[Item]
    responsible: Responsible
    ship_date: str
    status: str
    comment: str | None = None


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/notify")
def notify(request: Request, shipment: Shipment):
    auth = request.headers.get("Authorization")
    if auth != f"Bearer {WEBHOOK_SECRET}":
        raise HTTPException(status_code=403, detail="Forbidden")

    message = (
        f"🚚 <b>Отгрузка ТМЦ</b>\n\n"
        f"<b>Заказ:</b> {shipment.order_id}\n"
        f"<b>Получатель:</b> {shipment.recipient}\n"
        f"<b>Город:</b> {shipment.city}\n"
        f"<b>Телефон:</b> {shipment.phone}\n"
        f"<b>Дата отгрузки:</b> {shipment.ship_date}\n"
        f"<b>Статус:</b> {shipment.status}\n\n"
        f"<b>ТМЦ:</b>\n"
    )
    for item in shipment.items:
        message += f"• {item.name} — {item.qty} {item.unit}\n"

    if shipment.comment:
        message += f"\n📝 <b>Комментарий:</b> {shipment.comment}\n"

    if shipment.responsible.username:
        message += f"\n👤 Ответственный: @{shipment.responsible.username}"
    else:
        message += f"\n👤 Ответственный: {shipment.responsible.name}"

    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    )

    return {"ok": True, "sent_to": shipment.recipient}
