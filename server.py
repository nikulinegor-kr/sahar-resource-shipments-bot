import os
import html
import requests
from fastapi import FastAPI, Request, Header, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any

# === НАСТРОЙКИ ===
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
SHEET_SCRIPT_URL = os.getenv("SHEET_SCRIPT_URL", "").strip()  # ссылка на твой Web App из Apps Script

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

app = FastAPI(title="Snab Notify Bot", version="1.3.0")

# === МОДЕЛЬ ПОЛУЧЕНИЯ ДАННЫХ ===
class OrderPayload(BaseModel):
    order_id: Optional[str] = None
    recipient: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    ship_date: Optional[str] = None
    arrival: Optional[str] = None
    carrier: Optional[str] = None
    ttn: Optional[str] = None
    applicant: Optional[str] = None
    comment: Optional[str] = None
    special: Optional[str] = None  # доп. тип — согласование, получено и т.д.


# === ХЕЛПЕРЫ ===
def tg_send_message(text: str, buttons: Optional[list] = None) -> Dict[str, Any]:
    """Отправка сообщения с опциональными кнопками"""
    if not BOT_TOKEN or not CHAT_ID:
        return {"ok": False, "error": "BOT_TOKEN or CHAT_ID missing"}

    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}

    try:
        r = requests.post(f"{TG_API}/sendMessage", json=payload, timeout=10)
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tg_edit_message_reply_markup(chat_id: str, message_id: int):
    """Деактивация кнопок после нажатия"""
    url = f"{TG_API}/editMessageReplyMarkup"
    requests.post(url, json={"chat_id": chat_id, "message_id": message_id, "reply_markup": {}})


def update_sheet_status(order_id: str, new_status: str):
    """Отправляет запрос в Google Script для обновления статуса"""
    if not SHEET_SCRIPT_URL:
        return {"ok": False, "error": "SHEET_SCRIPT_URL not set"}

    payload = {"order_id": order_id, "status": new_status}
    try:
        res = requests.post(SHEET_SCRIPT_URL, json=payload, timeout=10)
        return {"ok": res.status_code == 200, "status_code": res.status_code, "text": res.text}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# === ФОРМАТИРОВАНИЕ ===
def format_order_text(data: Dict[str, Any]) -> (str, Optional[list]):
    get = lambda k: (data.get(k) or "").strip()
    special = (data.get("special") or "").strip().lower()

    # === Если требуется согласование ===
    if special == "approval_needed":
        order = get("order_id") or "—"
        text = (
            f"🧩 <b>ТРЕБУЕТСЯ СОГЛАСОВАНИЕ</b>\n"
            f"🧾 Заявка: {html.escape(order)}\n"
            f"👤 Заявитель: {html.escape(get('applicant') or '')}\n"
            f"💬 Комментарий: {html.escape(get('comment') or '')}"
        )
        buttons = [[{"text": "✅ СОГЛАСОВАНО", "callback_data": f"approve:{order}"}]]
        return text, buttons

    # === Обычное уведомление ===
    lines = ["📦 Уведомление о заявке"]
    order = get("order_id") or "—"
    lines.append(f"🧾 Заявка: {html.escape(order)}")
    if get("priority"): lines.append(f"⭐ Приоритет: {html.escape(get('priority'))}")
    if get("status"):   lines.append(f"🚚 Статус: {html.escape(get('status'))}")
    if get("carrier"):  lines.append(f"🚛 ТК: {html.escape(get('carrier'))}")
    if get("ttn"):      lines.append(f"📄 № ТТН: {html.escape(get('ttn'))}")
    if get("applicant"):lines.append(f"👤 Заявитель: {html.escape(get('applicant'))}")
    if get("comment"):  lines.append(f"📝 Комментарий: {html.escape(get('comment'))}")
    return "\n".join(lines), None


# === РОУТЫ ===
@app.get("/")
def root():
    return {"ok": True, "routes": ["/", "/health", "/tg (GET/POST)", "/notify", "/docs"]}

@app.get("/health")
def health():
    return {"ok": True, "service": "snab-bot", "webhook": "/tg"}

# Получение от таблицы
@app.post("/notify")
async def notify(req: Request, authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization.split("Bearer ")[-1].strip()
    if token != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid token")

    data = await req.json()
    text, buttons = format_order_text(data)
    tg_send_message(text, buttons)
    return {"ok": True}


# === КНОПКИ В ТЕЛЕГРАМ ===
@app.post("/tg")
async def telegram_webhook(req: Request):
    data = await req.json()
    if "callback_query" in data:
        cb = data["callback_query"]
        chat_id = cb["message"]["chat"]["id"]
        msg_id = cb["message"]["message_id"]
        user = cb["from"]["first_name"]
        payload = cb["data"]

        if payload.startswith("approve:"):
            order_id = payload.split("approve:")[-1]
            update_sheet_status(order_id, "В РАБОТУ: СОГЛАСОВАНО")
            tg_send_message(f"✅ {user} согласовал заявку <b>{order_id}</b>")
            tg_edit_message_reply_markup(chat_id, msg_id)

    return {"ok": True}
