# server.py
import os, html, json, requests
from typing import Optional, Dict, Any
from fastapi import FastAPI, Request, Header, HTTPException

BOT_TOKEN        = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID          = os.getenv("CHAT_ID", "").strip()
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "").strip()     # = CFG.SECRET в Apps Script
SHEET_SCRIPT_URL = os.getenv("SHEET_SCRIPT_URL", "").strip()   # URL веб-приложения Apps Script (.../exec)
SHEET_API_KEY    = os.getenv("SHEET_API_KEY", "").strip()      # = CFG.SECRET

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

app = FastAPI(title="Snab Notify Bot", version="1.2.1")

# --- Telegram helpers ---
def tg_send_message(text: str, kb: Optional[dict] = None) -> Dict[str, Any]:
    if not BOT_TOKEN or not CHAT_ID:
        return {"ok": False, "error": "BOT_TOKEN or CHAT_ID missing"}
    url = f"{TG_API}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if kb:
        payload["reply_markup"] = kb
    r = requests.post(url, json=payload, timeout=15)
    # полезно видеть, если TG отказал:
    try:
        j = r.json()
    except Exception:
        j = {"ok": False, "status_code": r.status_code, "text": r.text}
    return j

def tg_edit_markup(chat_id: int, message_id: int, kb: Optional[dict]) -> Dict[str, Any]:
    url = f"{TG_API}/editMessageReplyMarkup"
    payload = {"chat_id": chat_id, "message_id": message_id}
    if kb is not None:
        payload["reply_markup"] = kb
    r = requests.post(url, json=payload, timeout=10)
    try:
        return r.json()
    except Exception:
        return {"ok": False, "status_code": r.status_code, "text": r.text}

def tg_answer_callback(cb_id: str, text: str, alert: bool=False):
    url = f"{TG_API}/answerCallbackQuery"
    requests.post(url, json={"callback_query_id": cb_id, "text": text, "show_alert": alert}, timeout=10)

# --- Message formatting ---
def format_order_text(d: Dict[str, Any]) -> str:
    g = lambda k: (d.get(k) or "").strip()
    lines = ["📦 Уведомление о заявке"]
    lines.append(f"🧾 Заявка: {html.escape(g('order_id') or '—')}")
    if g("priority"):   lines.append(f"⭐ Приоритет: {html.escape(g('priority'))}")
    if g("status"):     lines.append(f"🚚 Статус: {html.escape(g('status'))}")
    if g("carrier"):    lines.append(f"🚛 ТК: {html.escape(g('carrier'))}")
    if g("ttn"):        lines.append(f"📄 № ТТН: {html.escape(g('ttn'))}")
    if g("ship_date"):  lines.append(f"📅 Дата отгрузки: {html.escape(g('ship_date'))}")
    if g("arrival"):    lines.append(f"📅 Дата прибытия: {html.escape(g('arrival'))}")
    if g("applicant"):  lines.append(f"👤 Заявитель: {html.escape(g('applicant'))}")
    if g("comment"):    lines.append(f"📝 Комментарий: {html.escape(g('comment'))}")
    return "\n".join(lines)

def ready_for_receive(status: str) -> bool:
    s = (status or "").lower()
    return ("доставлено" in s) and ("тк" in s)  # показываем кнопку только при «Доставлено в ТК»

# --- Service routes ---
@app.get("/")
def root():
    return {"ok": True, "routes": ["/", "/health", "/tg (GET/POST)", "/notify", "/docs"]}

@app.get("/health")
def health():
    return {"ok": True, "service": "snab-bot", "webhook": "/tg"}

@app.get("/tg")
def get_tg():
    return {"ok": True, "route": "/tg"}

# --- Entry from Apps Script ---
@app.post("/notify")
async def notify(req: Request, authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization.split("Bearer ")[-1].strip()
    if token != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        data = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Body must be JSON object")

    text = format_order_text(data)

    # >>> КНОПКА только для «Доставлено в ТК»
    kb = None
    if ready_for_receive(data.get("status", "")):
        row = data.get("row_index") or ""   # короткий идентификатор
        cb_data = f"rcv:{row}"              # <= коротко! (например: rcv:257)
        kb = {
            "inline_keyboard": [[
                { "text": "📦 ТМЦ ПОЛУЧЕНО", "callback_data": cb_data }
            ]]
        }

    res = tg_send_message(text, kb)
    # Если вдруг TG отказал — будет видно в логах
    return {"ok": True, "telegram_response": res}

# --- Telegram webhook ---
@app.post("/tg")
async def tg_post(req: Request):
    upd = await req.json()

    # Инлайн-кнопка
    if "callback_query" in upd:
        cq = upd["callback_query"]
        cb_id = cq.get("id")
        msg = cq.get("message", {}) or {}
        chat_id = msg.get("chat", {}).get("id")
        message_id = msg.get("message_id")
        data_raw = cq.get("data", "") or ""

        # ждём строго "rcv:<row>"
        if data_raw.startswith("rcv:"):
            row = data_raw.split("rcv:", 1)[-1].strip()

            if not SHEET_SCRIPT_URL or not SHEET_API_KEY:
                tg_answer_callback(cb_id, "Не настроен SHEET_SCRIPT_URL / SHEET_API_KEY", True)
                return {"ok": False}

            payload = {
                "apiKey": SHEET_API_KEY,
                "action": "set_received",
                "row": row
            }

            ok = False
            try:
                r = requests.post(SHEET_SCRIPT_URL, json=payload, timeout=20)
                js = r.json()
                ok = bool(js.get("ok"))
            except Exception as e:
                js = {"error": str(e)}

            # делаем кнопку неактивной, но видимой
            kb_disabled = { "inline_keyboard": [[ { "text": "✅ ТМЦ ПОЛУЧЕНО", "callback_data": "noop" } ]] }
            if chat_id and message_id:
                tg_edit_markup(chat_id, message_id, kb_disabled)

            tg_answer_callback(cb_id, "Статус обновлён" if ok else "Не удалось обновить статус")
            return {"ok": True, "result": js}

    # Прочие события / сообщения
    return {"ok": True}
