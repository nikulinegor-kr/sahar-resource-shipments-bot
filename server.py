import os, json, html, requests
from typing import Optional, Dict, Any
from fastapi import FastAPI, Request, Header, HTTPException

# === ENV ===
BOT_TOKEN       = os.getenv("BOT_TOKEN","").strip()
CHAT_ID         = os.getenv("CHAT_ID","").strip()
WEBHOOK_SECRET  = os.getenv("WEBHOOK_SECRET","").strip()

# для обработки нажатий (кнопка "ТМЦ ПОЛУЧЕНО")
SHEET_SCRIPT_URL = os.getenv("SHEET_SCRIPT_URL","").strip()   # URL web-app из Apps Script (doPost)
SHEET_API_KEY    = os.getenv("SHEET_API_KEY","").strip()      # тот же секрет, что и в CFG.SECRET в Apps Script

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

app = FastAPI(title="Snab Notify Bot", version="1.2.0")

# ================= Helpers =================

def norm(s: str) -> str:
    return (s or "").lower().replace("\u00a0"," ").replace("\t"," ").strip()

def is_delivered_to_tk(status: str) -> bool:
    n = norm(status)
    if not n:
        return False
    # допускаем разные пробелы/варианты
    if n == "доставлено в тк":
        return True
    return ("доставлено" in n) and ("в тк" in n)

def tg_send(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        r = requests.post(f"{TG_API}/{method}", json=payload, timeout=15)
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}

def send_message(text: str, reply_markup: Optional[dict]=None) -> Dict[str, Any]:
    if not BOT_TOKEN or not CHAT_ID:
        return {"ok": False, "error": "BOT_TOKEN or CHAT_ID missing"}
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg_send("sendMessage", payload)

def edit_message_reply_markup(chat_id: str, message_id: int, reply_markup: Optional[dict]) -> Dict[str, Any]:
    payload = {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup}
    return tg_send("editMessageReplyMarkup", payload)

def format_order_text(data: Dict[str, Any]) -> str:
    g = lambda k: (data.get(k) or "").strip()
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

# ================= Routes =================

@app.get("/")
def root():
    return {"ok": True, "routes": ["/", "/health", "/tg (GET/POST)", "/notify", "/docs"]}

@app.get("/health")
def health():
    return {"ok": True, "service": "snab-bot", "webhook": "/tg"}

@app.get("/tg")
def get_tg():
    return {"ok": True, "route": "/tg"}

# === Получение уведомления от Google Apps Script ===
@app.post("/notify")
async def notify(req: Request, authorization: Optional[str] = Header(None)):
    # Авторизация от таблицы
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if authorization.split(" ",1)[1].strip() != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        data = await req.json()
        if not isinstance(data, dict):
            raise ValueError("Body must be object")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    text = format_order_text(data)

    # Если статус "Доставлено в ТК" — добавляем "табличку" (инлайн-кнопку)
    reply_markup = None
    if is_delivered_to_tk(data.get("status","")) and data.get("order_id"):
        cb = {
            "type": "recv",
            "order_id": data["order_id"]
        }
        reply_markup = {
            "inline_keyboard": [[
                {"text": "✅ ТМЦ ПОЛУЧЕНО", "callback_data": json.dumps(cb, ensure_ascii=False)}
            ]]
        }

    tg_res = send_message(text, reply_markup=reply_markup)
    return {"ok": True, "telegram_response": tg_res}

# === Webhook Telegram: команды + нажатия кнопок ===
@app.post("/tg")
async def tg_post(req: Request):
    body = await req.json()
    # Нажатие на инлайн-кнопку
    if "callback_query" in body:
        cq = body["callback_query"]
        from_chat_id = str(cq["message"]["chat"]["id"])
        message_id   = int(cq["message"]["message_id"])
        data_raw     = cq.get("data","")
        try:
            data = json.loads(data_raw)
        except Exception:
            data = {}

        if data.get("type") == "recv" and data.get("order_id"):
            # запрос в Apps Script: подтвердить получение
            if not SHEET_SCRIPT_URL or not SHEET_API_KEY:
                tg_send("answerCallbackQuery", {
                    "callback_query_id": cq["id"],
                    "show_alert": True,
                    "text": "Ошибка конфигурации (нет SHEET_SCRIPT_URL/SHEET_API_KEY)"
                })
                return {"ok": True}

            try:
                r = requests.post(
                    SHEET_SCRIPT_URL,
                    json={
                        "action": "received",
                        "order_id": data["order_id"],
                        "api_key": SHEET_API_KEY
                    },
                    timeout=15
                )
                ok = (200 <= r.status_code < 300)
            except Exception:
                ok = False

            if ok:
                # “задизейблим” кнопку (оставим видимой, но неактивной)
                disabled = {
                    "inline_keyboard": [[
                        {"text": "✅ ТМЦ ПОЛУЧЕНО (отмечено)", "callback_data": "noop"}
                    ]]
                }
                edit_message_reply_markup(from_chat_id, message_id, disabled)
                tg_send("answerCallbackQuery", {
                    "callback_query_id": cq["id"],
                    "text": "Отмечено как получено ✅"
                })
            else:
                tg_send("answerCallbackQuery", {
                    "callback_query_id": cq["id"],
                    "show_alert": True,
                    "text": "Не удалось обновить статус"
                })
        return {"ok": True}

    # Обычные команды (/start, /help, /id)
    if "message" in body and "text" in body["message"]:
        chat_id = str(body["message"]["chat"]["id"])
        text = body["message"]["text"].strip().lower()
        if text.startswith("/start") or text.startswith("/help"):
            reply = ("Привет! Я бот снабжения.\n"
                     "Команды:\n"
                     "/help — список команд\n"
                     "/id — показать ваш Telegram ID")
            tg_send("sendMessage", {"chat_id": chat_id, "text": reply})
        elif text.startswith("/id"):
            uid = body["message"]["from"]["id"]
            uname = body["message"]["from"].get("username")
            reply = f"Ваш ID: <b>{uid}</b>"
            if uname:
                reply += f"\nПользователь: @{uname}"
            tg_send("sendMessage", {"chat_id": chat_id, "text": reply, "parse_mode":"HTML"})
        return {"ok": True}

    return {"ok": True}
