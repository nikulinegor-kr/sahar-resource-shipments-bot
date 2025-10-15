# server.py
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
import os, requests, html

app = FastAPI(title="Snab Bot & Notify", version="1.0.0")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID   = os.getenv("CHAT_ID", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
SHEET_CSV_URL  = os.getenv("SHEET_CSV_URL", "").strip()

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

def tg_send_message(chat_id: str, text: str) -> Dict[str, Any]:
    if not BOT_TOKEN:
        return {"ok": False, "error": "BOT_TOKEN is empty"}
    r = requests.post(
        f"{TG_API}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=15
    )
    try:
        return r.json()
    except Exception:
        return {"ok": False, "status": r.status_code, "text": r.text}

def esc(s: Optional[str]) -> str:
    return html.escape(s or "").strip()

@app.get("/")
def root():
    return {"ok": True, "routes": ["/", "/health", "/tg (GET/POST)", "/notify", "/docs"]}

@app.get("/health")
def health():
    return {"ok": True, "service": "snab-bot", "webhook": "/tg"}

@app.get("/tg")
def tg_probe():
    return {"ok": True, "route": "/tg"}

class NotifyPayload(BaseModel):
    order_id: Optional[str] = None
    recipient: Optional[str] = None
    priority: Optional[str] = None
    status:   Optional[str] = None
    ship_date: Optional[str] = None
    arrival_date: Optional[str] = None
    carrier: Optional[str] = None
    ttn: Optional[str] = None
    applicant: Optional[str] = None
    responsible: Optional[Dict[str, Any]] = None
    items: Optional[list] = []

def format_pretty(p: NotifyPayload) -> str:
    lines = ["📦 Уведомление о заявке", ""]
    if p.order_id:      lines.append(f"🧾 Заявка: {esc(p.order_id)}")
    if p.priority:      lines.append(f"⭐ Приоритет: {esc(p.priority)}")
    if p.status:        lines.append(f"🚚 Статус: {esc(p.status)}")
    if p.ship_date:     lines.append(f"📅 Дата отгрузки: {esc(p.ship_date)}")
    if p.arrival_date:  lines.append(f"📦 Дата прибытия: {esc(p.arrival_date)}")
    if p.carrier:       lines.append(f"🚛 ТК: {esc(p.carrier)}")
    if p.ttn:           lines.append(f"📄 № ТТН: {esc(p.ttn)}")
    if p.applicant:     lines.append(f"👤 Заявитель: {esc(p.applicant)}")
    return "\n".join(lines)

@app.post("/notify")
def notify(p: NotifyPayload):
    text = format_pretty(p)
    if CHAT_ID and BOT_TOKEN:
        tg_send_message(CHAT_ID, text)
    return {"ok": True, "preview": text}

@app.post("/tg")
async def tg_webhook(req: Request):
    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="BOT_TOKEN not configured")
    update = await req.json()
    try:
        msg = update.get("message") or update.get("channel_post") or {}
        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id", ""))

        txt = (msg.get("text") or "").strip()
        if txt.lower().startswith("/start"):
            reply = "👋 Бот снабжения готов. Доступные команды: /help /id"
        elif txt.lower().startswith("/help"):
            reply = (
                "🧰 Команды:\n"
                "/help — список команд\n"
                "/id — показать ваш Telegram ID\n"
            )
        elif txt.lower().startswith("/id"):
            reply = f"🪪 Ваш ID: {chat_id}"
        else:
            reply = "Я вас понял. Напишите /help"

        if chat_id:
            tg_send_message(chat_id, reply)

        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# catch-all — чтобы любые пути не падали 404 (для диагностики)
@app.get("/{path:path}")
def catch_all(path: str):
    return {"ok": True, "note": "catch-all route worked", "path": f"/{path}"}
