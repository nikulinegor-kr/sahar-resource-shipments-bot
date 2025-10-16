# server.py
import os, html, requests
from fastapi import FastAPI, Request, Header, HTTPException
from typing import Optional, Dict, Any

BOT_TOKEN        = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID          = os.getenv("CHAT_ID", "").strip()
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "").strip()
SHEET_SCRIPT_URL = os.getenv("SHEET_SCRIPT_URL", "").strip()
SHEET_API_KEY    = os.getenv("SHEET_API_KEY", "").strip()
BOT_USERNAME     = os.getenv("BOT_USERNAME", "").strip()

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

app = FastAPI(title="Snab Notify Bot", version="1.3.1")

def tg_call(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{TG_API}/{method}"
    r = requests.post(url, json=payload, timeout=15)
    try:
        return r.json()
    except Exception:
        return {"ok": False, "status": r.status_code, "text": r.text}

def tg_send(text: str, reply_markup: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg_call("sendMessage", payload)

def tg_edit_reply_markup(chat_id: str, message_id: int, reply_markup: Optional[Dict[str, Any]]):
    return tg_call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup})

def tg_answer_cbq(cbq_id: str, text: str, show_alert: bool = False):
    return tg_call("answerCallbackQuery", {"callback_query_id": cbq_id, "text": text, "show_alert": show_alert})

def render_text(data: Dict[str, Any]) -> str:
    g = lambda k: (data.get(k) or "").strip()
    lines = ["📦 Уведомление о заявке"]
    order = g("order_id") or "—"
    lines.append(f"🧾 Заявка: {html.escape(order)}")
    if g("priority"):   lines.append(f"⭐ Приоритет: {html.escape(g('priority'))}")
    if g("status"):     lines.append(f"🚚 Статус: {html.escape(g('status'))}")
    if g("carrier"):    lines.append(f"🚛 ТК: {html.escape(g('carrier'))}")
    if g("ttn"):        lines.append(f"📄 № ТТН: {html.escape(g('ttn'))}")
    if g("ship_date"):  lines.append(f"📅 Дата отгрузки: {html.escape(g('ship_date'))}")
    if g("arrival"):    lines.append(f"📅 Дата прибытия: {html.escape(g('arrival'))}")
    if g("applicant"):  lines.append(f"👤 Заявитель: {html.escape(g('applicant'))}")
    if g("comment"):    lines.append(f"📝 Комментарий: {html.escape(g('comment'))}")
    return "\n".join(lines)

def needs_button(status: str) -> bool:
    return (status or "").strip().lower() == "доставлено в тк"

@app.get("/")
def root():
    return {"ok": True, "routes": ["/", "/health", "/tg (GET/POST)", "/notify", "/docs"]}

@app.get("/health")
def health():
    return {"ok": True, "service": "snab-bot", "webhook": "/tg"}

@app.get("/tg")
def tg_get():
    return {"ok": True, "route": "/tg"}

@app.post("/notify")
async def notify(req: Request, authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization.split("Bearer ", 1)[-1].strip()
    if token != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        data = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    text = render_text(data)
    kb = None
    row_index = data.get("row_index")
    status = (data.get("status") or "")
    if row_index and needs_button(status):
        kb = {"inline_keyboard": [[
            {"text": "📦 ТМЦ ПОЛУЧЕНО", "callback_data": f"rcv:{int(row_index)}"}
        ]]}

    res = tg_send(text, reply_markup=kb)
    if not res.get("ok"):
        raise HTTPException(status_code=502, detail=f"Telegram error: {res}")
    return {"ok": True, "sent": True}

@app.post("/tg")
async def tg_webhook(req: Request):
    update = await req.json()

    # Кнопки
    if "callback_query" in update:
        cbq = update["callback_query"]
        data = cbq.get("data") or ""
        cbq_id = cbq.get("id")
        msg = cbq.get("message") or {}
        chat_id = str(msg.get("chat", {}).get("id") or CHAT_ID)
        message_id = msg.get("message_id")

        if data.startswith("rcv:"):
            try:
                row = int(data.split("rcv:",1)[1])
            except Exception:
                tg_answer_cbq(cbq_id, "Ошибка: некорректные данные.")
                return {"ok": True}

            if not SHEET_SCRIPT_URL or not SHEET_API_KEY:
                tg_answer_cbq(cbq_id, "Ошибка конфигурации сервера (нет URL/KEY).")
                return {"ok": True}

            # дергаем Apps Script
            try:
                r = requests.post(
                    SHEET_SCRIPT_URL,
                    json={"apiKey": SHEET_API_KEY, "action": "set_received", "row": row},
                    timeout=15
                )
                # попытаемся прочитать JSON и показать подробности
                ok = False
                err_text = f"http {r.status_code}"
                try:
                    j = r.json()
                    ok = bool(j.get("ok"))
                    if not ok:
                        err_text = j.get("error") or err_text
                except Exception:
                    err_text = r.text or err_text
            except Exception as e:
                ok = False
                err_text = str(e)

            if ok:
                tg_answer_cbq(cbq_id, "Статус обновлён: Доставлено.")
                if chat_id and message_id:
                    tg_edit_reply_markup(chat_id, message_id, reply_markup={})  # отключаем кнопку
            else:
                tg_answer_cbq(cbq_id, f"Не удалось обновить статус: {err_text}", show_alert=True)

        else:
            tg_answer_cbq(cbq_id, "Неизвестная команда.")
        return {"ok": True}

    # Базовые команды (минимум)
    msg = update.get("message") or update.get("channel_post") or {}
    text = (msg.get("text") or "").strip()
    low  = text.lower()
    if low in ("/start", f"/start@{BOT_USERNAME.lower()}" if BOT_USERNAME else "/start"):
        tg_send("Привет! Я бот уведомлений по заявкам. Команды: /today /week /my /priority /help")
    elif low in ("/help", f"/help@{BOT_USERNAME.lower()}" if BOT_USERNAME else "/help"):
        tg_send("Доступные команды:\n/today – отгрузки/прибытия сегодня\n/week – за 7 дней\n/my – мои заявки\n/priority – аварийные")
    return {"ok": True}
