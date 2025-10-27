# --- вверху файла ---
from fastapi import BackgroundTasks
import traceback
import time

# ...

def _safe_tg_send(text: str):
    """Фоновая отправка в Telegram с защитой от исключений и таймаутом."""
    if not BOT_TOKEN or not CHAT_ID:
        print("[tg] skip: no BOT_TOKEN/CHAT_ID")
        return
    try:
        r = requests.post(
            f"{TG_API}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=8,  # короткий таймаут, чтобы не вешать воркер
        )
        try:
            print("[tg] response:", r.status_code, r.text[:500])
        except Exception:
            pass
    except Exception as e:
        print("[tg] error:", repr(e))
        traceback.print_exc()

@app.post("/notify")
async def notify(
    req: Request,
    background: BackgroundTasks,
    authorization: Optional[str] = Header(None)
):
    t0 = time.time()
    # Авторизация
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization.split("Bearer ", 1)[-1].strip()
    if token != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Читаем JSON
    try:
        data = await req.json()
        if not isinstance(data, dict):
            raise ValueError("JSON is not an object")
    except Exception as e:
        print("[/notify] bad JSON:", repr(e))
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Формируем текст
    try:
        msg_text = format_order_text(data)
    except Exception as e:
        print("[/notify] format error:", repr(e))
        traceback.print_exc()
        msg_text = "📦 Уведомление"

    # Главное: отвечаем КЛИЕНТУ сразу,
    # а отправку в TG переносим в фоновую задачу.
    background.add_task(_safe_tg_send, msg_text)

    dt = round((time.time() - t0) * 1000)
    return {"ok": True, "queued": True, "elapsed_ms": dt}
# app.py
import os, html, requests
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Request, Header, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Snab Bot & Notify", version="1.0.0")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
SHEET_CSV_URL = os.getenv("SHEET_CSV_URL", "").strip()

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

def tg_send_message(chat_id: str | int, text: str, parse_mode: str = "HTML") -> Dict[str, Any]:
    if not BOT_TOKEN:
        return {"ok": False, "error": "BOT_TOKEN is empty"}
    r = requests.post(f"{TG_API}/sendMessage",
                      json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode,
                            "disable_web_page_preview": True}, timeout=15)
    try:
        return r.json()
    except Exception:
        return {"ok": False, "status": r.status_code, "text": r.text}

def esc(s: Optional[str]) -> str:
    return html.escape((s or "").strip())

# ==== сервисные ====
@app.get("/")
def root():
    return {"ok": True, "routes": ["/", "/health", "/tg (GET/POST)", "/notify"]}

@app.get("/health")
def health():
    return {"ok": True, "service": "snab-bot", "webhook": "/tg"}

@app.get("/tg")
def tg_get_probe():
    return {"ok": True, "route": "/tg"}

# ==== telegram webhook ====
@app.post("/tg")
async def tg_webhook(req: Request):
    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="BOT_TOKEN not configured")
    try:
        update = await req.json()
    except Exception:
        update = {}
    message = update.get("message") or update.get("channel_post")
    if not message:
        return {"ok": True, "skipped": True}
    chat_id = (message.get("chat") or {}).get("id")
    text = (message.get("text") or "").strip()

    if text.startswith("/start"):
        tg_send_message(chat_id, ("👋 <b>Бот снабжения</b> готов.\n\n"
                                  "Команды:\n• /help — список команд\n• /id — ваш Telegram ID"))
    elif text.startswith("/help"):
        tg_send_message(chat_id, ("<b>Команды:</b>\n"
                                  "• /start — начать работу\n"
                                  "• /help — список команд\n"
                                  "• /id — ваш Telegram ID\n\n"
                                  "Внешние уведомления приходят на /notify."))
    elif text.startswith("/id"):
        user = message.get("from", {})
        tg_send_message(chat_id, f"🆔 Ваш ID: <code>{user.get('id')}</code>\n"
                                 f"👤 @{esc(user.get('username')) if user.get('username') else '—'}")
    return {"ok": True}

# ==== /notify ====
class Responsible(BaseModel):
    name: Optional[str] = None
    username: Optional[str] = None
    user_id: Optional[int] = None

class Item(BaseModel):
    name: str
    qty: Optional[float] = None
    unit: Optional[str] = None

class NotifyPayload(BaseModel):
    order_id: str = Field(..., description="Номер заявки")
    recipient: str = Field(..., description="Получатель")
    city: Optional[str] = None
    phone: Optional[str] = None
    ship_date: Optional[str] = None
    arrival_date: Optional[str] = None
    carrier: Optional[str] = None
    ttn: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    applicant: Optional[str] = None
    comment: Optional[str] = None
    responsible: Optional[Responsible] = None
    items: List[Item] = []

def render_notify_message(p: NotifyPayload) -> str:
    parts = ["📦 <b>Уведомление о заявке</b>"]
    if p.order_id:     parts.append(f"\n🧾 <b>Заявка:</b> {esc(p.order_id)}")
    if p.priority:     parts.append(f"\n⭐ <b>Приоритет:</b> {esc(p.priority)}")
    if p.status:       parts.append(f"\n🚚 <b>Статус:</b> {esc(p.status)}")
    if p.ship_date:    parts.append(f"\n📅 <b>Дата отгрузки:</b> {esc(p.ship_date)}")
    if p.arrival_date: parts.append(f"\n📦 <b>Дата прибытия:</b> {esc(p.arrival_date)}")
    if p.carrier:      parts.append(f"\n🚛 <b>ТК:</b> {esc(p.carrier)}")
    if p.ttn:          parts.append(f"\n📄 <b>№ ТТН:</b> {esc(p.ttn)}")
    if p.applicant:    parts.append(f"\n👤 <b>Заявитель:</b> {esc(p.applicant)}")
    if p.responsible:
        r = p.responsible
        if r.username: parts.append(f"\n👤 <b>Ответственный:</b> @{esc(r.username)}")
        elif r.user_id: parts.append(f"\n👤 <b>Ответственный:</b> tg://user?id={r.user_id}")
        elif r.name: parts.append(f"\n👤 <b>Ответственный:</b> {esc(r.name)}")
    return "".join(parts)

@app.post("/notify")
async def notify(payload: NotifyPayload, authorization: str = Header(default="")):
    if WEBHOOK_SECRET and authorization != f"Bearer {WEBHOOK_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not CHAT_ID:
        raise HTTPException(status_code=500, detail="CHAT_ID is empty")
    msg = render_notify_message(payload)
    res = tg_send_message(CHAT_ID, msg)
    if not res.get("ok"):
        raise HTTPException(status_code=502, detail=f"Telegram error: {res}")
    return {"ok": True, "sent_to": CHAT_ID}
