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
SHEET_SCRIPT_URL = os.getenv("SHEET_SCRIPT_URL", "").strip()  # новый эндпоинт Apps Script
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

app = FastAPI(title="Snab Notify Bot", version="1.2.0")

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


# === TELEGRAM ===
def tg_send_message(text: str, buttons=None) -> Dict[str, Any]:
    if not BOT_TOKEN or not CHAT_ID:
        return {"ok": False, "error": "BOT_TOKEN or CHAT_ID missing"}

    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}

    r = requests.post(f"{TG_API}/sendMessage", json=payload)
    return r.json()


def tg_answer_callback(cb_id, text="✅ Обновлено"):
    requests.post(f"{TG_API}/answerCallbackQuery", json={
        "callback_query_id": cb_id,
        "text": text,
        "show_alert": False
    })


def tg_edit_message(chat_id, msg_id, new_text):
    requests.post(f"{TG_API}/editMessageText", json={
        "chat_id": chat_id,
        "message_id": msg_id,
        "text": new_text,
        "parse_mode": "HTML"
    })


# === СООБЩЕНИЕ ===
def format_order_text(data: Dict[str, Any]) -> str:
    get = lambda k: (data.get(k) or "").strip()

    lines = ["📦 Уведомление о заявке"]
    lines.append(f"🧾 Заявка: {html.escape(get('order_id') or '—')}")
    if get("priority"):
        lines.append(f"⭐ Приоритет: {html.escape(get('priority'))}")
    if get("status"):
        lines.append(f"🚚 Статус: {html.escape(get('status'))}")
    if get("carrier"):
        lines.append(f"🚛 ТК: {html.escape(get('carrier'))}")
    if get("ttn"):
        lines.append(f"📄 № ТТН: {html.escape(get('ttn'))}")
    if get("ship_date"):
        lines.append(f"📅 Дата отгрузки: {html.escape(get('ship_date'))}")
    if get("arrival"):
        lines.append(f"📅 Дата прибытия: {html.escape(get('arrival'))}")
    if get("applicant"):
        lines.append(f"👤 Заявитель: {html.escape(get('applicant'))}")
    if get("comment"):
        lines.append(f"📝 Комментарий: {html.escape(get('comment'))}")

    return "\n".join(lines)


# === РОУТЫ ===
@app.get("/")
def root():
    return {"ok": True, "routes": ["/", "/health", "/notify", "/tg (GET/POST)", "/docs"]}


@app.get("/health")
def health():
    return {"ok": True, "service": "snab-bot", "version": "1.2.0"}


# === УВЕДОМЛЕНИЕ ОТ APPS SCRIPT ===
@app.post("/notify")
async def notify(req: Request, authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    token = authorization.split("Bearer ")[-1].strip()
    if token != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid token")

    data = await req.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Body must be JSON object")

    msg_text = format_order_text(data)
    order_id = data.get("order_id", "")
    buttons = [
        [{"text": "✅ Получено", "callback_data": f"received|{order_id}"}]
    ]
    res = tg_send_message(msg_text, buttons)
    return {"ok": True, "telegram_response": res}


# === CALLBACK ОТ КНОПКИ ===
@app.post("/tg")
async def telegram_webhook(req: Request):
    body = await req.json()
    print("TG update:", body)

    if "callback_query" in body:
        cb = body["callback_query"]
        data = cb.get("data", "")
        chat_id = cb["message"]["chat"]["id"]
        msg_id = cb["message"]["message_id"]

        if data.startswith("received|"):
            order_id = data.split("|", 1)[-1]
            _ = mark_delivered(order_id)
            new_text = cb["message"]["text"] + "\n\n✅ Отметка: Доставлено"
            tg_edit_message(chat_id, msg_id, new_text)
            tg_answer_callback(cb["id"], "Статус обновлён: Доставлено")

    return {"ok": True}


# === ОТПРАВКА В GOOGLE SCRIPT ДЛЯ ОБНОВЛЕНИЯ СТАТУСА ===
def mark_delivered(order_id: str):
    """Посылает Apps Script сигнал обновить статус по Заявке."""
    if not SHEET_SCRIPT_URL:
        return {"ok": False, "error": "SHEET_SCRIPT_URL missing"}

    try:
        res = requests.post(SHEET_SCRIPT_URL, json={"order_id": order_id, "status": "Доставлено"}, timeout=10)
        return {"ok": True, "code": res.status_code}
    except Exception as e:
        return {"ok": False, "error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000)
