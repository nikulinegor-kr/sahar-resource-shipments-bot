import os
import html
import requests
from fastapi import FastAPI, Request, Header, HTTPException
from typing import Optional, Dict, Any

# ========= НАСТРОЙКИ (через переменные окружения Koyeb) =========
BOT_TOKEN        = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID          = os.getenv("CHAT_ID", "").strip()              # id группы/топика, куда слать
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "").strip()       # секрет для /notify (из таблицы)
SHEET_SCRIPT_URL = os.getenv("SHEET_SCRIPT_URL", "").strip()     # URL веб-приложения Apps Script (Deploy→Web app)
SHEET_API_KEY    = os.getenv("SHEET_API_KEY", "").strip()        # ключ для авторизации из бота в таблицу

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

app = FastAPI(title="SnabNotifyBot", version="2.0.0")

# для «НА ДОРАБОТКУ»: ожидаем комментарий от конкретного пользователя
PENDING_REVISE: Dict[int, str] = {}  # user_id -> order_id


# ========= УТИЛИТЫ =========
def tg_send_message(text: str, reply_markup: Optional[Dict] = None, parse_mode: str = "HTML"):
    if not BOT_TOKEN or not CHAT_ID:
        print("⚠️ tg_send_message: BOT_TOKEN/CHAT_ID missing")
        return {"ok": False, "reason": "no token/chat"}
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    r = requests.post(f"{TG_API}/sendMessage", json=payload, timeout=10)
    try:
        return r.json()
    except Exception:
        return {"ok": False, "status_code": r.status_code, "text": r.text}


def tg_edit_reply_markup(chat_id: int, message_id: int, reply_markup: Optional[Dict]):
    # обнуляем клавиатуру после нажатия
    payload = {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup or {}}
    r = requests.post(f"{TG_API}/editMessageReplyMarkup", json=payload, timeout=10)
    return r.json()


def tg_answer_callback(callback_id: str, text: str):
    requests.post(f"{TG_API}/answerCallbackQuery", json={"callback_query_id": callback_id, "text": text}, timeout=10)


def fmt_user(u: Dict[str, Any]) -> str:
    username = u.get("username")
    if username:
        return f"@{username}"
    first = (u.get("first_name") or "").strip()
    last  = (u.get("last_name") or "").strip()
    return (first + (" " + last if last else "")).strip() or f"id:{u.get('id')}"


def update_sheet_status(order_id: str, new_status: str, comment: Optional[str] = None):
    if not SHEET_SCRIPT_URL or not SHEET_API_KEY:
        print("⚠️ update_sheet_status: SHEET_SCRIPT_URL/SHEET_API_KEY missing")
        return {"ok": False, "reason": "sheet creds missing"}

    body = {"action": "status", "order_id": order_id, "new_status": new_status}
    if comment:
        body = {"action": "status_with_comment", "order_id": order_id, "new_status": new_status, "comment": comment}

    r = requests.post(
        SHEET_SCRIPT_URL,
        headers={"Authorization": f"Bearer {SHEET_API_KEY}"},
        json=body,
        timeout=12
    )
    print("Sheet update:", r.status_code, r.text)
    try:
        return r.json()
    except Exception:
        return {"ok": False, "status_code": r.status_code, "text": r.text}


def safe(x):  # безопасный геттер + эскейп
    return html.escape((x or "").strip())


# ========= КЛАВИАТУРЫ (инлайн-кнопки) =========
def build_keyboard_for_comment(comment: str, order_id: str):
    c = (comment or "").lower().strip()
    if "требуется согласование" in c:
        # три кнопки: В РАБОТУ / НА ДОРАБОТКУ / ОТКЛОНЕНО
        return {
            "inline_keyboard": [
                [{"text": "✅ В РАБОТУ", "callback_data": f"approve|{order_id}"}],
                [{"text": "🔧 НА ДОРАБОТКУ", "callback_data": f"revise|{order_id}"}],
                [{"text": "❌ ОТКЛОНЕНО", "callback_data": f"reject|{order_id}"}],
            ]
        }
    return None


def build_keyboard_for_status(status_or_comment: str, order_id: str):
    s = (status_or_comment or "").lower().strip()
    # кнопка ПОЛУЧЕНО показываем для кейса «доставлено в тк»
    if "доставлено в тк" in s:
        return {"inline_keyboard": [[{"text": "📦 ТМЦ ПОЛУЧЕНО", "callback_data": f"received|{order_id}"}]]}
    return None


# ========= /notify (получает JSON из Apps Script) =========
@app.post("/notify")
async def notify(req: Request, authorization: Optional[str] = Header(None)):
    # проверка секрета
    if authorization != f"Bearer {WEBHOOK_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    data = await req.json()

    order_id = (data.get("order_id") or "").strip()
    status   = (data.get("status") or "").strip()
    prio     = (data.get("priority") or "").strip()
    ship     = (data.get("ship_date") or "").strip()
    arrival  = (data.get("arrival") or "").strip()
    carrier  = (data.get("carrier") or "").strip()
    ttn      = (data.get("ttn") or "").strip()
    appl     = (data.get("applicant") or "").strip()
    comment  = (data.get("comment") or "").strip()

    # красивый текст
    lines = ["📦 <b>Уведомление о заявке</b>"]
    if order_id: lines.append(f"🧾 <b>Заявка:</b> {safe(order_id)}")
    if prio:     lines.append(f"⭐ <b>Приоритет:</b> {safe(prio)}")
    if status:   lines.append(f"🚚 <b>Статус:</b> {safe(status)}")
    if carrier:  lines.append(f"🚛 <b>ТК:</b> {safe(carrier)}")
    if ttn:      lines.append(f"📄 <b>№ ТТН:</b> {safe(ttn)}")
    if ship:     lines.append(f"📅 <b>Дата отгрузки:</b> {safe(ship)}")
    if arrival:  lines.append(f"📅 <b>Дата прибытия:</b> {safe(arrival)}")
    if appl:     lines.append(f"👤 <b>Заявитель:</b> {safe(appl)}")
    if comment:  lines.append(f"📝 <b>Комментарий:</b> {safe(comment)}")

    text = "\n".join(lines)

    # клавиатуры по условиям
    kb = build_keyboard_for_comment(comment, order_id)
    if not kb:
        kb = build_keyboard_for_status(status or comment, order_id)

    res = tg_send_message(text, reply_markup=kb)
    return {"ok": True, "sent": res}


# ========= Telegram webhook (клики и ответы) =========
@app.post("/tg")
async def tg_webhook(req: Request):
    upd = await req.json()
    print("TG update:", str(upd)[:1000])

    # --- обработка нажатия на кнопку ---
    if "callback_query" in upd:
        cq   = upd["callback_query"]
        data = (cq.get("data") or "")
        user = cq.get("from", {})
        chat = cq.get("message", {}).get("chat", {})
        mid  = cq.get("message", {}).get("message_id")
        cbid = cq.get("id")

        parts = data.split("|", 1)
        if len(parts) != 2:
            return {"ok": True}

        action, order_id = parts
        who = fmt_user(user)

        # выключим клавиатуру в исходном сообщении
        try:
            tg_edit_reply_markup(chat_id=chat["id"], message_id=mid, reply_markup=None)
        except Exception as e:
            print("edit markup error:", e)

        # ответить Telegram, чтобы исчез «часик» на кнопке
        try:
            tg_answer_callback(cbid, "Готово")
        except Exception:
            pass

        # обработка статусов
        if action == "received":
            # ТМЦ ПОЛУЧЕНО → ставим в таблице ДОСТАВЛЕНО
            update_sheet_status(order_id, "ДОСТАВЛЕНО")
            tg_send_message(f"📦 <b>ТМЦ ПОЛУЧЕНО</b> по заявке <b>{html.escape(order_id)}</b>\nНажал: {who}")

        elif action == "approve":
            update_sheet_status(order_id, "В РАБОТУ")
            tg_send_message(f"✅ <b>В РАБОТУ</b> по заявке <b>{html.escape(order_id)}</b>\nНажал: {who}")

        elif action == "reject":
            update_sheet_status(order_id, "ОТКЛОНЕНО")
            tg_send_message(f"❌ <b>ОТКЛОНЕНО</b> по заявке <b>{html.escape(order_id)}</b>\nНажал: {who}")

        elif action == "revise":
            # ждём комментарий в следующем сообщении пользователя
            PENDING_REVISE[user.get("id")] = order_id
            tg_send_message(
                f"🔧 Для заявки <b>{html.escape(order_id)}</b> выбрано <b>НА ДОРАБОТКУ</b>.\n"
                f"{who}, отправь сюда комментарий — он будет записан в таблицу."
            )

        return {"ok": True}

    # --- пользователь прислал комментарий (для «НА ДОРАБОТКУ») ---
    if "message" in upd:
        msg  = upd["message"]
        uid  = msg.get("from", {}).get("id")
        text = (msg.get("text") or "").strip()

        if uid in PENDING_REVISE and text:
            order_id = PENDING_REVISE.pop(uid)
            update_sheet_status(order_id, "НА ДОРАБОТКУ", comment=text)
            tg_send_message(
                f"🔧 <b>НА ДОРАБОТКУ</b> по заявке <b>{html.escape(order_id)}</b>\n"
                f"Комментарий: {html.escape(text)}"
            )
        return {"ok": True}

    return {"ok": True}


# ========= Health =========
@app.get("/health")
def health():
    return {"ok": True, "service": "snab-bot", "webhook": "/tg"}
