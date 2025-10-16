import os, html, json, re, requests
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, Request, Header, HTTPException

# ==== ENV ====
BOT_TOKEN       = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID         = os.getenv("CHAT_ID", "").strip()         # -100...
WEBHOOK_SECRET  = os.getenv("WEBHOOK_SECRET", "").strip()  # sahar2025secure_longtoken
SHEET_SCRIPT_URL= os.getenv("SHEET_SCRIPT_URL", "").strip()# WebApp URL из Apps Script (Anyone)
TG_API          = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

app = FastAPI(title="Snab Notify Bot", version="2.0.0")

# ==== Утилиты ====
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).replace("\u00A0", " ").strip().lower()

def _is_delivered_to_tk(status: str) -> bool:
    ns = _norm(status)
    if not ns:
        return False
    if ns == "доставлено в тк":
        return True
    # любые варианты "доставлено ... в тк"
    return re.search(r"(^|\s)доставлено(\s|$)", ns) and re.search(r"(\s|^)в\s*тк(\s|$)", ns)

def _format_message(data: Dict[str, Any]) -> str:
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

def _make_keyboard(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Возвращает reply_markup c нужными кнопками или None."""
    buttons: List[List[Dict[str, str]]] = []

    status = data.get("status") or ""
    special = (data.get("special") or "").strip()

    # Кнопка «ТМЦ ПОЛУЧЕНО» при Доставлено в ТК
    if _is_delivered_to_tk(status):
        order = (data.get("order_id") or "").strip()
        if order:
            buttons.append([{
                "text": "✅ ТМЦ ПОЛУЧЕНО",
                "callback_data": json.dumps({"action":"received","order_id":order})
            }])

    # Кнопка «СОГЛАСОВАНО» при special=approval_needed
    if special == "approval_needed":
        order = (data.get("order_id") or "").strip()
        if order:
            buttons.append([{
                "text": "✅ СОГЛАСОВАНО",
                "callback_data": json.dumps({"action":"approved","order_id":order})
            }])

    if not buttons:
        return None

    return {"inline_keyboard": buttons}

def tg_send(text: str, reply_markup: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not BOT_TOKEN or not CHAT_ID:
        return {"ok": False, "error": "BOT_TOKEN or CHAT_ID missing"}
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    r = requests.post(f"{TG_API}/sendMessage", json=payload, timeout=15)
    return r.json()

def tg_answer_callback(callback_query_id: str, text: str, show_alert: bool=False):
    if not BOT_TOKEN:
        return
    requests.post(f"{TG_API}/answerCallbackQuery",
                  json={"callback_query_id": callback_query_id, "text": text, "show_alert": show_alert},
                  timeout=10)

def tg_edit_reply_markup(chat_id: str, message_id: int):
    """Удаляем клавиатуру после нажатия (делаем кнопку неактивной)."""
    if not BOT_TOKEN:
        return
    requests.post(f"{TG_API}/editMessageReplyMarkup",
                  json={"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard":[]}},
                  timeout=10)

# ==== Служебные ручки ====
@app.get("/")
def root():
    return {"ok": True, "routes": ["/", "/health", "/notify", "/tg (GET/POST)"]}

@app.get("/health")
def health():
    return {"ok": True, "service": "snab-bot", "webhook": "/tg"}

@app.get("/tg")
def tg_get():
    return {"ok": True, "route": "/tg"}

# ==== Получение из таблицы ====
@app.post("/notify")
async def notify(req: Request, authorization: Optional[str] = Header(None)):
    # авторизация
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization.split("Bearer ",1)[1].strip()
    if token != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        data = await req.json()
        assert isinstance(data, dict)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    text = _format_message(data)
    kb = _make_keyboard(data)
    resp = tg_send(text, kb)
    return {"ok": True, "telegram_response": resp, "with_keyboard": bool(kb)}

# ==== Telegram webhook ====
@app.post("/tg")
async def tg_webhook(req: Request):
    update = await req.json()
    # Логи для отладки:
    print("TG update:", json.dumps(update, ensure_ascii=False))

    # 1) Кнопки (callback_query)
    if "callback_query" in update:
        cq = update["callback_query"]
        cq_id = str(cq.get("id"))
        from_user = cq.get("from", {})
        msg = cq.get("message", {})
        chat = msg.get("chat", {})
        chat_id = str(chat.get("id"))
        message_id = int(msg.get("message_id"))
        data_raw = cq.get("data") or "{}"

        try:
            data = json.loads(data_raw)
        except Exception:
            data = {}

        action = data.get("action")
        order_id = (data.get("order_id") or "").strip()

        # без WebApp URL меняем только клавиатуру и говорим пользователю
        if not SHEET_SCRIPT_URL:
            tg_answer_callback(cq_id, "Нет SHEET_SCRIPT_URL на сервере")
            tg_edit_reply_markup(chat_id, message_id)
            return {"ok": True}

        # вызываем Apps Script (обновить таблицу)
        try:
            r = requests.post(SHEET_SCRIPT_URL, json={
                "action": action,
                "order_id": order_id,
                "from_user_id": from_user.get("id"),
                "from_user": from_user.get("username") or (from_user.get("first_name","")+" "+from_user.get("last_name","")).strip(),
            }, timeout=15)
            ok = (r.status_code == 200)
            if ok:
                tg_answer_callback(cq_id, "Готово ✅")
                tg_edit_reply_markup(chat_id, message_id)  # выключаем кнопку(и)
            else:
                tg_answer_callback(cq_id, f"Ошибка: {r.status_code}")
        except Exception as e:
            tg_answer_callback(cq_id, f"Ошибка запроса: {e}")

        return {"ok": True}

    # 2) Команды /текст
    msg = update.get("message") or update.get("channel_post")
    if msg and "text" in msg:
        text = msg["text"].strip()
        if text.startswith("/start"):
            requests.post(f"{TG_API}/sendMessage", json={
                "chat_id": msg["chat"]["id"],
                "text": (
                    "Привет! Я бот снабжения.\n\n"
                    "Я присылаю уведомления из Google Таблицы и показываю кнопки:\n"
                    "— «✅ ТМЦ ПОЛУЧЕНО» при статусе «Доставлено в ТК»\n"
                    "— «✅ СОГЛАСОВАНО» если в комментарии стоит «ТРЕБУЕТСЯ СОГЛАСОВАНИЕ»\n"
                )
            }, timeout=10)

    return {"ok": True}
