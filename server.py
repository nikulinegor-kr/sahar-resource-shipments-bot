# server.py
import os, html, requests, re
from typing import Optional, Dict, Any
from fastapi import FastAPI, Request, Header, HTTPException

# === ENV ===
BOT_TOKEN        = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID          = os.getenv("CHAT_ID", "").strip()                 # -1003141855190
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "").strip()          # sahar2025secure_longtoken
SHEET_SCRIPT_URL = os.getenv("SHEET_SCRIPT_URL", "").strip()        # URL веб-приложения Apps Script (Anyone)
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

app = FastAPI(title="Snab Notify Bot", version="1.3.0")


# ====== УТИЛЫ ======
def norm(s: Optional[str]) -> str:
    if s is None: return ""
    return re.sub(r"\s+", " ", s.replace("\u00A0", " ")).strip().lower()

def send_tg(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not BOT_TOKEN or not CHAT_ID:
        return {"ok": False, "error": "BOT_TOKEN/CHAT_ID missing"}
    r = requests.post(f"{TG_API}/sendMessage", json=payload, timeout=15)
    try:
        return r.json()
    except Exception:
        return {"ok": False, "error": r.text}

def edit_reply_markup(chat_id: str, message_id: int, reply_markup: Optional[Dict[str, Any]]):
    url = f"{TG_API}/editMessageReplyMarkup"
    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": reply_markup
    }
    requests.post(url, json=data, timeout=10)

def answer_cbq(cbq_id: str, text: str, show_alert: bool=False):
    requests.post(f"{TG_API}/answerCallbackQuery",
                  json={"callback_query_id": cbq_id, "text": text, "show_alert": show_alert},
                  timeout=10)

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

def need_btn_received(status_text: str) -> bool:
    s = norm(status_text)
    # ловим «доставлено в тк» с разными пробелами/точками
    return bool(re.search(r"\bдоставлено\b", s) and re.search(r"\bв\s*т[.\s]*к\b", s))

def need_btn_approve(comment_text: str) -> bool:
    s = norm(comment_text)
    return "требуется согласование" in s


# ====== СЛУЖЕБНЫЕ РОУТЫ ======
@app.get("/")
def root():
    return {"ok": True, "routes": ["/", "/health", "/tg (GET/POST)", "/notify", "/docs"]}

@app.get("/health")
def health():
    return {"ok": True, "service": "snab-bot", "webhook": "/tg"}

@app.get("/tg")
def tg_get():
    return {"ok": True, "route": "/tg"}


# ====== ПРИЁМ ИЗ ТАБЛИЦЫ ======
@app.post("/notify")
async def notify(req: Request, authorization: Optional[str] = Header(None)):
    # 1) авторизация
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization.split("Bearer ")[-1].strip()
    if token != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid token")

    # 2) json
    try:
        data = await req.json()
        assert isinstance(data, dict)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # 3) текст
    text = format_order_text(data)

    # 4) клавиатура по условиям
    kb: Optional[Dict[str, Any]] = None
    buttons = []

    if need_btn_received(data.get("status") or ""):
        # кнопка «ТМЦ получено»
        order_id = (data.get("order_id") or "").strip()
        if order_id:
            buttons.append([{"text": "📦 ТМЦ ПОЛУЧЕНО", "callback_data": f"recv|{order_id}"}])

    if need_btn_approve(data.get("comment") or ""):
        order_id = (data.get("order_id") or "").strip()
        if order_id:
            buttons.append([{"text": "✅ В РАБОТУ • СОГЛАСОВАНО", "callback_data": f"appr|{order_id}"}])

    if buttons:
        kb = {"inline_keyboard": buttons}

    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if kb:
        payload["reply_markup"] = kb

    res = send_tg(payload)
    if not res.get("ok"):
        raise HTTPException(status_code=502, detail=f"Telegram error: {res}")

    return {"ok": True, "telegram_response": res}


# ====== ВЕБХУК ОТ TELEGRAM (команды и клики по инлайн-кнопкам) ======
@app.post("/tg")
async def tg_post(req: Request):
    upd = await req.json()
    print("TG update:", upd)

    # 1) команды / текст
    msg = upd.get("message") or upd.get("channel_post")
    if msg and isinstance(msg, dict):
        text = (msg.get("text") or "").strip()
        if text.startswith("/start") or text.startswith("/help"):
            help_text = (
                "Привет! Я бот снабжения.\n\n"
                "Доступные команды:\n"
                "/start — помощь\n"
                "/help — помощь\n"
                "(уведомления приходят автоматически из таблицы)"
            )
            requests.post(f"{TG_API}/sendMessage",
                          json={"chat_id": msg["chat"]["id"], "text": help_text},
                          timeout=10)
            return {"ok": True}

    # 2) callback_query (кнопки)
    cbq = upd.get("callback_query")
    if cbq and isinstance(cbq, dict):
        cbq_id = cbq.get("id")
        from_user = cbq.get("from", {})
        msg_obj   = cbq.get("message", {})
        data      = (cbq.get("data") or "").strip()
        chat_id   = str(msg_obj.get("chat", {}).get("id"))
        message_id = msg_obj.get("message_id")

        # ожидаем форматы:
        # recv|ORDER_ID   -> поставить "Доставлено"
        # appr|ORDER_ID   -> поставить "В РАБОТУ: СОГЛАСОВАНО"
        if "|" in data:
            action, order_id = data.split("|", 1)
            order_id = order_id.strip()

            if action in ("recv", "appr") and order_id:
                if not SHEET_SCRIPT_URL:
                    answer_cbq(cbq_id, "Ошибка: не настроен SHEET_SCRIPT_URL", True)
                    return {"ok": False}

                new_status = "Доставлено" if action == "recv" else "В РАБОТУ: СОГЛАСОВАНО"
                # POST в Apps Script
                try:
                    r = requests.post(
                        SHEET_SCRIPT_URL,
                        json={
                            "secret": WEBHOOK_SECRET,
                            "action": "set_status",
                            "order_id": order_id,
                            "status": new_status
                        },
                        timeout=15
                    )
                    ok = r.ok
                    try:
                        j = r.json()
                        ok = ok and j.get("ok") is True
                    except Exception:
                        pass

                    if ok:
                        answer_cbq(cbq_id, "Статус обновлён ✅")
                        # деактивируем кнопку (заменим на «зеленую» метку)
                        if action == "recv":
                            new_kb = {"inline_keyboard": [[{"text": "✅ Получено", "callback_data": "done"}]]}
                        else:
                            new_kb = {"inline_keyboard": [[{"text": "✅ Согласовано", "callback_data": "done"}]]}
                        if chat_id and message_id:
                            edit_reply_markup(chat_id, message_id, new_kb)
                        return {"ok": True}
                    else:
                        answer_cbq(cbq_id, "Не удалось обновить статус", True)
                        return {"ok": False}
                except Exception as e:
                    answer_cbq(cbq_id, f"Ошибка запроса: {e}", True)
                    return {"ok": False}

        # если что-то иное — просто ответим
        answer_cbq(cbq_id, "Ок")
        return {"ok": True}

    return {"ok": True}
