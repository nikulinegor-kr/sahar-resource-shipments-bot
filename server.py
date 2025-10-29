import os
import html
import requests
from typing import Optional, Dict, Any, Tuple
from fastapi import FastAPI, Request, Header, HTTPException

app = FastAPI(title="SnabOrdersBot", version="2.2.0")

# ===== ENV =====
BOT_TOKEN        = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID          = os.getenv("CHAT_ID", "").strip()                  # -100...
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "").strip()           # для /notify
SHEET_SCRIPT_URL = os.getenv("SHEET_SCRIPT_URL", "").strip()         # WebApp Apps Script
SHEET_API_KEY    = os.getenv("SHEET_API_KEY", "").strip()            # Bearer ключ для Apps Script

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Константы статусов
RECEIVED_STATUS = "Доставлено"
APPROVED_STATUS = "В РАБОТУ: СОГЛАСОВАНО"
REJECTED_STATUS = "ОТКЛОНЕНО"

# Храним, кто запросил «На доработку», чтобы следующее сообщение считалось комментарием
# key = (chat_id, user_id) -> order_id
pending_comments: Dict[Tuple[int, int], str] = {}

# ========= helpers =========

def display_name(u: dict) -> str:
    uname = u.get("username")
    if uname:
        return f"@{uname}"
    fn = u.get("first_name", "")
    ln = u.get("last_name", "")
    full = " ".join(x for x in [fn, ln] if x).strip()
    return full or str(u.get("id"))

def tg_send_message(text: str, reply_markup: Optional[dict] = None, parse_mode="HTML"):
    if not BOT_TOKEN or not CHAT_ID:
        print("⚠️ BOT_TOKEN/CHAT_ID not set"); return
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    if reply_markup: payload["reply_markup"] = reply_markup
    try:
        r = requests.post(f"{TG_API}/sendMessage", json=payload, timeout=15)
        return r.json()
    except Exception as e:
        print("tg_send_message error:", e)

def tg_edit_reply_markup(chat_id: int, message_id: int, reply_markup: Optional[dict]):
    try:
        requests.post(f"{TG_API}/editMessageReplyMarkup",
                      json={"chat_id": chat_id, "message_id": message_id,
                            "reply_markup": reply_markup or {}},
                      timeout=10)
    except Exception as e:
        print("tg_edit_reply_markup error:", e)

def update_sheet_status(order_id: str, new_status: str, comment: Optional[str] = None):
    """Бьём в Apps Script Web App (Bearer SHEET_API_KEY)."""
    if not SHEET_SCRIPT_URL or not SHEET_API_KEY:
        print("⚠️ SHEET_SCRIPT_URL/SHEET_API_KEY not set"); return
    body = {
        "action": "status_with_comment" if comment else "update_status",
        "order_id": order_id,
        "new_status": new_status
    }
    if comment:
        body["comment"] = comment
    try:
        r = requests.post(
            SHEET_SCRIPT_URL,
            headers={"Authorization": f"Bearer {SHEET_API_KEY}"},
            json=body,
            timeout=20
        )
        print("Sheet update:", r.status_code, r.text[:300])
    except Exception as e:
        print("update_sheet_status error:", e)

def norm(s: str) -> str:
    return (s or "").lower().replace("\u00A0"," ").strip()

def is_delivered_trigger(status: str, comment: str) -> bool:
    s = norm(status); c = norm(comment)
    return ("доставлено в тк" in s) or ("доставлено в тк" in c)

def is_approval_needed(comment: str) -> bool:
    return "требуется согласование" in norm(comment)

def kb_received(order_id: str):
    return {"inline_keyboard":[
        [{"text":"📦 ТМЦ ПОЛУЧЕНО", "callback_data": f"received|{order_id}"}]
    ]}

def kb_approval(order_id: str):
    # Три строки — чтобы уместилось в телефоне
    return {"inline_keyboard":[
        [{"text":"✅ В РАБОТУ",      "callback_data": f"approve|{order_id}"}],
        [{"text":"🔧 НА ДОРАБОТКУ", "callback_data": f"revise|{order_id}"}],
        [{"text":"❌ ОТКЛОНЕНО",    "callback_data": f"reject|{order_id}"}],
    ]}

def kb_disabled(title: str):
    # «заблокированная» клавиатура
    return {"inline_keyboard":[
        [{"text": f"🔒 {title}", "callback_data": "noop"}]
    ]}

def build_message(d: dict) -> Tuple[str, Optional[dict]]:
    """Текст уведомления + подходящая клавиатура."""
    get = lambda k: (d.get(k) or "").strip()
    lines = ["📦 <b>Уведомление о заявке</b>"]
    if get("order_id"):   lines.append(f"🧾 <b>Заявка:</b> {html.escape(get('order_id'))}")
    if get("priority"):   lines.append(f"⭐ <b>Приоритет:</b> {html.escape(get('priority'))}")
    if get("status"):     lines.append(f"🚚 <b>Статус:</b> {html.escape(get('status'))}")
    if get("carrier"):    lines.append(f"🚛 <b>ТК:</b> {html.escape(get('carrier'))}")
    if get("ttn"):        lines.append(f"📄 <b>№ ТТН:</b> {html.escape(get('ttn'))}")
    if get("applicant"):  lines.append(f"👤 <b>Заявитель:</b> {html.escape(get('applicant'))}")
    if get("comment"):    lines.append(f"📝 <b>Комментарий:</b> {html.escape(get('comment'))}")
    text = "\n".join(lines)

    kb = None
    if is_approval_needed(get("comment")):
        kb = kb_approval(get("order_id"))
    elif is_delivered_trigger(get("status"), get("comment")):
        kb = kb_received(get("order_id"))
    return text, kb

# ========= endpoints =========

@app.get("/health")
def health():
    return {"ok": True, "service": "snaborders-bot"}

@app.post("/notify")
async def notify(req: Request, authorization: Optional[str] = Header(None)):
    if authorization != f"Bearer {WEBHOOK_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")
    data = await req.json()
    text, kb = build_message(data)
    tg_send_message(text, reply_markup=kb)
    return {"ok": True}

@app.post("/tg")
async def tg_webhook(req: Request):
    upd = await req.json()
    print("TG update:", str(upd)[:500])

    # --- callback buttons ---
    if "callback_query" in upd:
        cq = upd["callback_query"]
        data = cq.get("data","")
        msg  = cq.get("message", {})
        chat_id = msg.get("chat",{}).get("id")
        message_id = msg.get("message_id")
        u = cq.get("from", {})
        who = display_name(u)

        if "|" in data:
            action, order_id = data.split("|",1)
        else:
            return {"ok": True}

        # Блокируем клавиатуру сразу
        title_map = {
            "received":"ТМЦ ПОЛУЧЕНО",
            "approve":"В РАБОТУ",
            "revise":"НА ДОРАБОТКУ",
            "reject":"ОТКЛОНЕНО"
        }
        tg_edit_reply_markup(chat_id, message_id, kb_disabled(title_map.get(action, "Обработано")))

        if action == "received":
            update_sheet_status(order_id, RECEIVED_STATUS)
            tg_send_message(f"📦 {who} отметил(а) заявку <b>{html.escape(order_id)}</b> как полученную.")

        elif action == "approve":
            update_sheet_status(order_id, APPROVED_STATUS)
            tg_send_message(f"✅ {who} согласовал(а) заявку <b>{html.escape(order_id)}</b> — отправлено в работу.")

        elif action == "reject":
            update_sheet_status(order_id, REJECTED_STATUS)
            tg_send_message(f"❌ {who} отклонил(а) заявку <b>{html.escape(order_id)}</b>.")

        elif action == "revise":
            # ждём отдельный текст от пользователя
            pending_comments[(chat_id, u.get("id"))] = order_id
            tg_send_message(
                f"🔧 {who}, пришлите сообщением комментарий для заявки <b>{html.escape(order_id)}</b>.\n"
                f"Он будет записан в таблицу вместе со статусом «На доработку».")
        return {"ok": True}

    # --- обычное сообщение (ловим комментарий для «На доработку») ---
    if "message" in upd:
        m = upd["message"]
        chat_id = m.get("chat",{}).get("id")
        user_id = m.get("from",{}).get("id")
        text = (m.get("text") or "").strip()
        key = (chat_id, user_id)
        if key in pending_comments and text:
            order_id = pending_comments.pop(key)
            new_status = f"На доработку"
            update_sheet_status(order_id, new_status, comment=text)
            who = display_name(m.get("from",{}))
            tg_send_message(f"🔧 {who} отправил(а) заявку <b>{html.escape(order_id)}</b> на доработку.\n"
                            f"Комментарий: {html.escape(text)}")
        return {"ok": True}

    return {"ok": True}
