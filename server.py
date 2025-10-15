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
SHEET_CSV_URL = os.getenv("SHEET_CSV_URL", "").strip()

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

app = FastAPI(title="Snab Notify Bot", version="1.1.0")

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


# === ХЕЛПЕР: отправка в Telegram ===
def tg_send_message(text: str, parse_mode: str = "HTML") -> Dict[str, Any]:
    if not BOT_TOKEN or not CHAT_ID:
        return {"ok": False, "error": "BOT_TOKEN or CHAT_ID missing"}

    url = f"{TG_API}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }

    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


# === ФОРМАТИРОВАНИЕ ТЕКСТА ===
def format_order_text(data: Dict[str, Any]) -> str:
    """Собирает красивое сообщение о заявке."""
    get = lambda k: (data.get(k) or "").strip()

    lines = ["📦 Уведомление о заявке"]
    order = get("order_id") or "—"
    lines.append(f"🧾 Заявка: {html.escape(order)}")

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


# === СЛУЖЕБНЫЕ РОУТЫ ===
@app.get("/")
def root():
    return {"ok": True, "routes": ["/", "/health", "/tg (GET/POST)", "/notify", "/docs"]}


@app.get("/health")
def health():
    return {"ok": True, "service": "snab-bot", "webhook": "/tg"}


@app.get("/tg")
def get_tg():
    return {"ok": True, "route": "/tg"}


# === ПОЛУЧЕНИЕ ДАННЫХ ИЗ GOOGLE SCRIPT ===
@app.post("/notify")
async def notify(req: Request, authorization: Optional[str] = Header(None)):
    # Проверяем секрет
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    token = authorization.split("Bearer ")[-1].strip()
    if token != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Разбираем JSON
    try:
        data = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Body must be JSON object")

    msg_text = format_order_text(data)
    res = tg_send_message(msg_text)
    return {"ok": True, "telegram_response": res}


# === Telegram Webhook (опционально) ===
@app.post("/tg")
async def telegram_webhook(req: Request):
    data = await req.json()
    print("TG webhook received:", data)
    return {"ok": True, "received": True}


# === ЛОКАЛЬНЫЙ ЗАПУСК ===
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000)
