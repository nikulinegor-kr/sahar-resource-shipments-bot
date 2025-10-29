import os
import html
import requests
from fastapi import FastAPI, Request, Header, HTTPException
from typing import Optional, Dict, Any
from threading import Thread

app = FastAPI(title="Snab Notify Bot", version="2.3.0")

# ===== ENV (настрой в Koyeb) =====
BOT_TOKEN        = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID          = os.getenv("CHAT_ID", "").strip()               # -100xxxxxxxxxx
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "").strip()        # должен совпадать с CFG.SECRET в Apps Script (для /notify)
SHEET_SCRIPT_URL = os.getenv("SHEET_SCRIPT_URL", "").strip()      # URL веб-приложения Apps Script (Deploy → Web app → URL)
SHEET_API_KEY    = os.getenv("SHEET_API_KEY", "").strip()         # ключ для doPost в Apps Script (совпадает с CFG.SECRET)

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

# Ждём комментарий для «На доработку»
pending_comments: Dict[int, str] = {}  # {user_id: order_id}


# ======== Telegram helpers ========
def tg_send_message(text: str, reply_markup: Optional[Dict] = None, parse_mode: str = "HTML"):
    if not BOT_TOKEN or not CHAT_ID:
        print("⚠️ BOT_TOKEN or CHAT_ID missing")
        return {"ok": False, "error": "BOT_TOKEN/CHAT_ID missing"}
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        r = requests.post(f"{TG_API}/sendMessage", json=payload, timeout=10)
        return r.json()
    except Exception as e:
        print("tg_send_message error:", e)
        return {"ok": False, "error": str(e)}

def tg_answer_callback_query(callback_query_id: str, text: str = "", show_alert: bool = False):
    if not BOT_TOKEN:
        return
    try:
        requests.post(f"{TG_API}/answerCallbackQuery",
                      json={"callback_query_id": callback_query_id, "text": text, "show_alert": show_alert},
                      timeout=8)
    except Exception as e:
        print("answerCallback error:", e)

def tg_edit_reply_markup(chat_id: int, message_id: int):
    """Удалить/отключить клавиатуру у исходного сообщения."""
    if not BOT_TOKEN:
        return
    try:
        requests.post(f"{TG_API}/editMessageReplyMarkup",
                      json={"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}},
                      timeout=8)
    except Exception as e:
        print("editReplyMarkup error:", e)

def user_display_name(u: Dict[str, Any]) -> str:
    first = u.get("first_name") or ""
    last  = u.get("last_name") or ""
    full  = (first + " " + last).strip()
    if full:
        return full
    return u.get("username") or str(u.get("id") or "user")


# ======== Sheets helpers ========
def update_sheet_status(order_id: str, new_status: str, comment: Optional[str] = None):
    """
    Обновляем статус (и опц. комментарий) в Google Sheets через Apps Script doPost.
    Тело JSON:
      {
        "api_key": SHEET_API_KEY,   # обязателен
        "action": "status" | "status_with_comment",
        "order_id": "...",
        "new_status": "...",
        "comment": "..."            # опционально
      }
    """
    if not SHEET_SCRIPT_URL or not SHEET_API_KEY:
        print("⚠️ SHEET_SCRIPT_URL or SHEET_API_KEY missing")
        return

    payload = {
        "api_key": SHEET_API_KEY,
        "order_id": order_id,
    }

    if comment:
        payload.update({"action": "status_with_comment", "new_status": new_status, "comment": comment})
    else:
        payload.update({"action": "status", "new_status": new_status})

    try:
        res = requests.post(SHEET_SCRIPT_URL, json=payload, timeout=12)
        print("Sheet update:", res.status_code, res.text)
    except Exception as e:
        print("update_sheet_status error:", e)


# ======== Формат уведомления ========
def format_order_text(data: Dict[str, Any]) -> str:
    g = lambda k: (data.get(k) or "").strip()
    lines = ["📦 <b>Уведомление о заявке</b>"]
    lines.append(f"🧾 <b>Заявка:</b> {html.escape(g('order_id') or '—')}")
    if g("priority"): lines.append(f"⭐ <b>Приоритет:</b> {html.escape(g('priority'))}")
    if g("status"):   lines.append(f"🚚 <b>Статус:</b> {html.escape(g('status'))}")
    if g("carrier"):  lines.append(f"🚛 <b>ТК:</b> {html.escape(g('carrier'))}")
    if g("ttn"):      lines.append(f"📄 <b>№ ТТН:</b> {html.escape(g('ttn'))}")
    if g("ship_date"):lines.append(f"📅 <b>Дата отгрузки:</b> {html.escape(g('ship_date'))}")
    if g("arrival"):  lines.append(f"📅 <b>Дата прибытия:</b> {html.escape(g('arrival'))}")
    if g("applicant"):lines.append(f"👤 <b>Заявитель:</b> {html.escape(g('applicant'))}")
    if g("comment"):  lines.append(f"📝 <b>Комментарий:</b> {html.escape(g('comment'))}")
    return "\n".join(lines)

def build_keyboard(comment: str, status: str, order_id: str) -> Optional[Dict]:
    """
    Вертикальные кнопки:
      - если в комментарии есть "ТРЕБУЕТСЯ СОГЛАСОВАНИЕ" → кнопки Согласовано / На доработку / Отклонено
      - если статус "Доставлено в ТК" → кнопка "ТМЦ ПОЛУЧЕНО"
    """
    c = (comment or "").lower()
    s = (status or "").lower()

    if "требуется согласование" in c:
        return {
            "inline_keyboard": [
                [{"text": "✅ В РАБОТУ: СОГЛАСОВАНО", "callback_data": f"approve|{order_id}"}],
                [{"text": "🔧 НА ДОРАБОТКУ",           "callback_data": f"revise|{order_id}"}],
                [{"text": "❌ ОТКЛОНЕНО",              "callback_data": f"reject|{order_id}"}],
            ]
        }

    if "доставлено в тк" in s:
        return {
            "inline_keyboard": [
                [{"text": "📦 ТМЦ ПОЛУЧЕНО", "callback_data": f"received|{order_id}"}]
            ]
        }

    return None


# ======== Роуты ========
@app.get("/")
def root():
    return {"ok": True, "routes": ["/", "/health", "/notify", "/tg (GET/POST)", "/docs"]}

@app.get("/health")
def health():
    return {"ok": True, "service": "snab-bot", "webhook": "/tg", "notify": "/notify"}

@app.post("/notify")
async def notify(req: Request, authorization: Optional[str] = Header(None)):
    # Авторизация от Apps Script (Bearer WEBHOOK_SECRET)
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization.split("Bearer ", 1)[-1].strip()
    if token != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid token")

    data = await req.json()
    text = format_order_text(data)
    kb = build_keyboard(
        comment=data.get("comment", ""),
        status=data.get("status", ""),
        order_id=data.get("order_id", "")
    )

    # отправляем в чат
    Thread(target=lambda: tg_send_message(text, reply_markup=kb)).start()
    return {"ok": True}

@app.post("/tg")
async def tg_webhook(req: Request):
    update = await req.json()
    print("TG update:", update)

    # Нажатие на inline-кнопку
    if "callback_query" in update:
        cq = update["callback_query"]
        cb_id = cq.get("id")
        data  = cq.get("data", "")
        msg   = cq.get("message", {})
        chat  = msg.get("chat", {})
        chat_id = chat.get("id")
        message_id = msg.get("message_id")
        user = cq.get("from", {})
        user_name = user_display_name(user)

        try:
            action, order_id = data.split("|", 1)
        except ValueError:
            tg_answer_callback_query(cb_id, "Некорректные данные")
            return {"ok": False}

        # отключаем кнопки у исходного сообщения
        if chat_id and message_id:
            tg_edit_reply_markup(chat_id, message_id)

        if action == "approve":
            new_status = "В РАБОТУ: СОГЛАСОВАНО"
            update_sheet_status(order_id, new_status)
            tg_answer_callback_query(cb_id, "Согласовано ✅")
            tg_send_message(f"✅ Заявка <b>{html.escape(order_id)}</b> согласована.\n👤 Исполнитель: <b>{html.escape(user_name)}</b>")

        elif action == "reject":
            new_status = "ОТКЛОНЕНО"
            update_sheet_status(order_id, new_status)
            tg_answer_callback_query(cb_id, "Отклонено ❌")
            tg_send_message(f"❌ Заявка <b>{html.escape(order_id)}</b> отклонена.\n👤 Решение: <b>{html.escape(user_name)}</b>")

        elif action == "revise":
            # ждём текст следующим сообщением от нажавшего
            pending_comments[user.get("id")] = order_id
            tg_answer_callback_query(cb_id, "Укажите доработки 🔧")
            tg_send_message(
                f"🔧 Для заявки <b>{html.escape(order_id)}</b> требуется доработка.\n"
                f"👤 <b>{html.escape(user_name)}</b>, ответьте сюда текстом — он попадёт в комментарий."
            )

        elif action == "received":
            # в таблицу пишем ровно «Доставлено»
            new_status = "Доставлено"
            update_sheet_status(order_id, new_status)
            tg_answer_callback_query(cb_id, "ТМЦ получено 📦")
            tg_send_message(
                f"📦 ТМЦ по заявке <b>{html.escape(order_id)}</b> получено.\n"
                f"📋 Статус в таблице: <b>{new_status}</b>\n"
                f"👤 Отметил: <b>{html.escape(user_name)}</b>"
            )

        else:
            tg_answer_callback_query(cb_id, "Неизвестное действие")
        return {"ok": True}

    # Ответ текстом для "На доработку"
    if "message" in update:
        msg = update["message"]
        user = msg.get("from", {})
        text = (msg.get("text") or "").strip()
        if not text:
            return {"ok": True}

        uid = user.get("id")
        if uid in pending_comments:
            order_id = pending_comments.pop(uid)
            new_status = "На доработку"
            update_sheet_status(order_id, new_status, comment=text)
            tg_send_message(
                f"🔧 Заявка <b>{html.escape(order_id)}</b> отправлена на доработку.\n"
                f"💬 Комментарий: {html.escape(text)}\n"
                f"👤 От: <b>{html.escape(user_display_name(user))}</b>"
            )
        return {"ok": True}

    return {"ok": True}
