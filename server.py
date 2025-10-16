import os, json, re, html, requests
from typing import Optional, Dict, Any

from fastapi import FastAPI, Request, Header, HTTPException
from pydantic import BaseModel

# ========= ENV =========
BOT_TOKEN        = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID          = os.getenv("CHAT_ID", "").strip()
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "").strip()

# куда постим при нажатии кнопки
SHEET_SCRIPT_URL = os.getenv("SHEET_SCRIPT_URL", "").strip()  # Web App URL из Apps Script
SHEET_API_KEY    = os.getenv("SHEET_API_KEY", "").strip()     # тот же ключ, что CFG.API_KEY в скрипте

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

app = FastAPI(title="Snab Notify Bot", version="1.3.0")


# ========= MODELS =========
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
    # можно расширить при необходимости


# ========= TG helpers =========
def tg_request(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{TG_API}/{method}"
    r = requests.post(url, json=payload, timeout=15)
    try:
        return r.json()
    except Exception:
        return {"ok": False, "error": r.text}

def tg_send_message(text: str, reply_markup: Optional[Dict]=None, parse_mode: str="HTML") -> Dict[str, Any]:
    if not BOT_TOKEN or not CHAT_ID:
        return {"ok": False, "error": "BOT_TOKEN or CHAT_ID missing"}
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg_request("sendMessage", payload)

def tg_edit_reply_markup(chat_id: str, message_id: int, reply_markup: Optional[Dict]) -> Dict[str, Any]:
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": reply_markup or {"inline_keyboard": []}
    }
    return tg_request("editMessageReplyMarkup", payload)

def tg_answer_callback(cb_id: str, text: str="", show_alert: bool=False):
    return tg_request("answerCallbackQuery", {"callback_query_id": cb_id, "text": text, "show_alert": show_alert})


# ========= Message formatting =========
def format_order_text(data: Dict[str, Any]) -> str:
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

def _norm(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())

def should_show_received_button(status: Optional[str]) -> bool:
    """Показываем кнопку только при статусе 'Доставлено в ТК' (учитываем варианты написаний)."""
    st = _norm(status)
    candidates = {
        "доставлено в тк",
        "доставлено в т к",
        "доставлено в т.к.",
        "в тк доставлено",
    }
    return st in candidates

def build_received_keyboard(order_id: Optional[str]) -> Dict[str, Any]:
    """
    Делает инлайн-клавиатуру с одной кнопкой.
    callback_data — JSON, бот обрабатывает тип 'rcv' (received).
    """
    data = {"t": "rcv"}
    if order_id:
        data["order_id"] = order_id
    return {
        "inline_keyboard": [[
            {"text": "✅ ТМЦ ПОЛУЧЕНО", "callback_data": json.dumps(data, ensure_ascii=False)}
        ]]
    }

def build_received_done_keyboard() -> Dict[str, Any]:
    """Неактивная версия: оставляем кнопку, но с типом 'done' — бот отвечает 'уже отмечено'."""
    data = {"t": "done"}
    return {
        "inline_keyboard": [[
            {"text": "✅ ТМЦ ПОЛУЧЕНО", "callback_data": json.dumps(data, ensure_ascii=False)}
        ]]
    }


# ========= Service routes =========
@app.get("/")
def root():
    return {"ok": True, "routes": ["/", "/health", "/tg (GET/POST)", "/notify", "/docs"]}

@app.get("/health")
def health():
    has_cfg = bool(SHEET_SCRIPT_URL and SHEET_API_KEY)
    return {"ok": True, "service": "snab-bot", "webhook": "/tg", "sheet_cfg": has_cfg}

@app.get("/tg")
def tg_get():
    return {"ok": True, "route": "/tg"}

# =========== Receive notifications from Apps Script and post to Telegram ===========
@app.post("/notify")
async def notify(req: Request, authorization: Optional[str] = Header(None)):
    # simple bearer check so постить сюда может только Apps Script
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if authorization.split(" ", 1)[1].strip() != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        data = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Body must be JSON object")

    text = format_order_text(data)

    # решаем — показывать ли кнопку
    markup = None
    if should_show_received_button(data.get("status")):
        markup = build_received_keyboard(data.get("order_id"))

    res = tg_send_message(text, reply_markup=markup)
    return {"ok": True, "telegram_response": res, "with_button": bool(markup)}


# =========== Telegram webhook: commands + button clicks ===========
@app.post("/tg")
async def telegram_webhook(req: Request):
    update = await req.json()
    # print("TG webhook:", json.dumps(update, ensure_ascii=False))

    # Команды
    if "message" in update and update["message"].get("text"):
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        text = msg["text"].strip()
        user = msg.get("from", {})

        if text.startswith("/start"):
            reply = ("Привет! Я бот снабжения.\n"
                     "Команды: /help — список команд, /id — показать ваш Telegram ID")
            return tg_request("sendMessage", {"chat_id": chat_id, "text": reply})

        if text.startswith("/help"):
            reply = ("Доступные команды:\n"
                     "/start — начать\n"
                     "/help — список команд\n"
                     "/id — показать ваш Telegram ID")
            return tg_request("sendMessage", {"chat_id": chat_id, "text": reply})

        if text.startswith("/id"):
            uname = "@" + user.get("username") if user.get("username") else f"{user.get('first_name','')}".strip()
            reply = f"Ваш ID: <b>{user.get('id')}</b>\nПользователь: {html.escape(uname)}"
            return tg_request("sendMessage", {"chat_id": chat_id, "text": reply, "parse_mode":"HTML"})

        # игнор прочего
        return {"ok": True}

    # Клики по инлайн-кнопкам
    if "callback_query" in update:
        cb = update["callback_query"]
        cb_id = cb["id"]
        msg = cb["message"]
        chat_id = msg["chat"]["id"]
        message_id = msg["message_id"]

        try:
            payload = json.loads(cb.get("data") or "{}")
        except Exception:
            payload = {}

        typ = payload.get("t")

        # кнопка уже отключена — просто сообщим
        if typ == "done":
            tg_answer_callback(cb_id, "Уже отмечено ✅")
            return {"ok": True}

        # основная кнопка «ТМЦ ПОЛУЧЕНО»
        if typ == "rcv":
            if not SHEET_SCRIPT_URL or not SHEET_API_KEY:
                tg_answer_callback(cb_id, "Ошибка конфигурации (нет SHEET_SCRIPT_URL/SHEET_API_KEY)", True)
                return {"ok": False, "err": "no sheet cfg"}

            order_id = payload.get("order_id", "")

            # шлём в Apps Script (он теперь умеет и без order_id, но лучше передавать если есть)
            body = {
                "api_key":   SHEET_API_KEY,
                "action":    "received",
                "order_id":  order_id,
                "new_status": "Доставлено",
            }
            try:
                r = requests.post(SHEET_SCRIPT_URL, json=body, timeout=20)
                ok = r.status_code == 200 and (r.json().get("ok") is True)
            except Exception as e:
                ok = False

            if not ok:
                tg_answer_callback(cb_id, "Не удалось обновить статус", True)
                return {"ok": False}

            # успех: отвечаем, и меняем клавиатуру на «неактивную»
            tg_answer_callback(cb_id, "Статус обновлён: Доставлено ✅")
            tg_edit_reply_markup(str(chat_id), int(message_id), build_received_done_keyboard())
            return {"ok": True}

        # неизвестный тип
        tg_answer_callback(cb_id, "Неизвестное действие", False)
        return {"ok": False, "err": "unknown cb type"}

    return {"ok": True}
