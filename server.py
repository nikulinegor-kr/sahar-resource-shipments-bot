import os
import re
import html
import requests
from fastapi import FastAPI, Request, Header, HTTPException
from typing import Optional, Dict, Any

app = FastAPI(title="SnabOrders Bot", version="2.4")

# ===== ENV =====
BOT_TOKEN        = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID          = os.getenv("CHAT_ID", "").strip()          # id группы (-100…)
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "").strip()   # = SECRET в Apps Script
SHEET_SCRIPT_URL = os.getenv("SHEET_SCRIPT_URL", "").strip() # Web App из Apps Script
SHEET_API_KEY    = os.getenv("SHEET_API_KEY", "").strip()    # = SECRET (тот же токен)

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# user_id -> order_id (для режима "НА ДОРАБОТКУ")
PENDING_REVISE: Dict[int, str] = {}

# ---------- TG helpers ----------
def tg_call(method: str, payload: Dict[str, Any]):
    try:
        r = requests.post(f"{TG_API}/{method}", json=payload, timeout=15)
        print("TG", method, "→", r.status_code, r.text[:200])
        return r.json()
    except Exception as e:
        print("TG error:", e)
        return {"ok": False, "error": str(e)}

def tg_send_message(text: str, reply_markup: Optional[Dict]=None, parse_mode: str="HTML"):
    if not BOT_TOKEN or not CHAT_ID:
        print("TG: missing BOT_TOKEN/CHAT_ID")
        return
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    print("SEND MSG:", text[:120].replace("\n"," | "))
    tg_call("sendMessage", payload)

def tg_edit_reply_markup(chat_id: int, message_id: int, reply_markup: Optional[Dict]):
    tg_call("editMessageReplyMarkup", {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": reply_markup
    })

def tg_edit_message_text(chat_id: int, message_id: int, new_text: str, parse_mode: str="HTML"):
    tg_call("editMessageText", {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": new_text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    })

def tg_answer_callback_query(cq_id: str, text: str = "", show_alert: bool = False):
    tg_call("answerCallbackQuery", {
        "callback_query_id": cq_id,
        "text": text,
        "show_alert": show_alert
    })

def fmt_user(u: Dict[str, Any]) -> str:
    uname = u.get("username")
    if uname:
        return f"@{uname}"
    first = (u.get("first_name") or "").strip()
    last  = (u.get("last_name")  or "").strip()
    full  = (first + " " + last).strip()
    return html.escape(full) if full else f"id:{u.get('id')}"

# ---------- SHEET helpers ----------
def sheet_update_status(order_id: str, new_status: str, comment: Optional[str]=None):
    if not SHEET_SCRIPT_URL or not SHEET_API_KEY:
        print("SHEET: missing SHEET_SCRIPT_URL/SHEET_API_KEY")
        return {"ok": False, "error": "config"}

    payload: Dict[str, Any] = {
        "action": "update_status",
        "order_id": order_id,
        "new_status": new_status
    }
    if comment is not None:
        payload["action"] = "status_with_comment"
        payload["comment"] = comment

    try:
        r = requests.post(
            SHEET_SCRIPT_URL,
            headers={"Authorization": f"Bearer {SHEET_API_KEY}"},
            json=payload,
            timeout=12
        )
        print("sheet_update_status:", r.status_code, r.text[:200])
        if r.headers.get("content-type","").startswith("application/json"):
            return r.json()
        return {"ok": r.ok}
    except Exception as e:
        print("sheet_update_status error:", e)
        return {"ok": False, "error": str(e)}

# ---------- helpers ----------
def norm(s: str) -> str:
    return (s or "").lower().replace("\u00a0"," ").strip()

def extract_invoice_url(raw: str) -> str:
    """Пробуем вытащить URL из строки (формула HYPERLINK или просто ссылка)."""
    if not raw:
        return ""
    raw = raw.strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    m = re.search(r"https?://[^\s\")]+", raw)
    return m.group(0) if m else ""

# ---------- текст уведомления ----------
def make_message(data: Dict[str, Any]) -> str:
    get = lambda k: (data.get(k) or "").strip()
    lines = ["📦 <b>Уведомление о заявке</b>"]
    if get("order_id"):
        lines.append(f"🧾 <b>Заявка:</b> {html.escape(get('order_id'))}")
    if get("priority"):
        lines.append(f"⭐ <b>Приоритет:</b> {html.escape(get('priority'))}")
    if get("status"):
        lines.append(f"🚚 <b>Статус:</b> {html.escape(get('status'))}")
    if get("carrier"):
        lines.append(f"🚛 <b>ТК:</b> {html.escape(get('carrier'))}")
    if get("ttn"):
        lines.append(f"📄 <b>№ ТТН:</b> {html.escape(get('ttn'))}")
    if get("ship_date"):
        lines.append(f"📅 <b>Дата отгрузки:</b> {html.escape(get('ship_date'))}")
    if get("arrival"):
        lines.append(f"📅 <b>Дата прибытия:</b> {html.escape(get('arrival'))}")
    if get("applicant"):
        lines.append(f"👤 <b>Заявитель:</b> {html.escape(get('applicant'))}")
    if get("comment"):
        lines.append(f"📝 <b>Комментарий:</b> {html.escape(get('comment'))}")
    return "\n".join(lines)

def build_keyboard(data: Dict[str, Any]) -> Optional[Dict]:
    st = norm(data.get("status",""))
    cm = norm(data.get("comment",""))
    invoice_raw = (data.get("invoice") or "").strip()
    invoice_url = extract_invoice_url(invoice_raw)

    keyboard: Dict[str, Any] = {"inline_keyboard": []}

    # кнопка "ТМЦ ПОЛУЧЕНО"
    if "доставлено в тк" in st:
        keyboard["inline_keyboard"].append(
            [{"text": "📦 ТМЦ ПОЛУЧЕНО", "callback_data": f"received|{data.get('order_id','')}"}]
        )

    # блок согласования
    if "требуется согласование" in cm:
        order = data.get("order_id","")
        keyboard["inline_keyboard"].append(
            [{"text": "✅ В РАБОТУ", "callback_data": f"approve|{order}"}]
        )
        keyboard["inline_keyboard"].append(
            [{"text": "🔧 НА ДОРАБОТКУ", "callback_data": f"revise|{order}"}]
        )
        keyboard["inline_keyboard"].append(
            [{"text": "❌ ОТКЛОНЕНО", "callback_data": f"reject|{order}"}]
        )

    # кнопка "Открыть счёт"
    if invoice_url:
        keyboard["inline_keyboard"].append(
            [{"text": "📄 Открыть счёт", "url": invoice_url}]
        )

    return keyboard if keyboard["inline_keyboard"] else None

# ---------- ROUTES ----------
@app.get("/")
def root():
    return {"ok": True, "routes": ["/", "/health", "/notify", "/tg"]}

@app.get("/health")
def health():
    return {"ok": True, "service": "snaborders-bot"}

# прилетает из Google Apps Script
@app.post("/notify")
async def notify(req: Request, authorization: Optional[str] = Header(None)):
    if authorization != f"Bearer {WEBHOOK_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")
    data = await req.json()
    text = make_message(data)
    kb   = build_keyboard(data)
    tg_send_message(text, reply_markup=kb)
    return {"ok": True}

# Telegram webhook
@app.post("/tg")
async def tg_webhook(req: Request):
    upd = await req.json()
    print("TG update:", str(upd)[:800])

    # --- callback-кнопки ---
    if "callback_query" in upd:
        cq        = upd["callback_query"]
        cq_id     = cq.get("id")
        user      = cq.get("from", {}) or {}
        message   = cq.get("message", {}) or {}
        chat      = message.get("chat", {}) or {}
        mid       = message.get("message_id")
        orig_text = message.get("text") or ""
        data_raw  = (cq.get("data") or "")
        who       = fmt_user(user)

        if data_raw in ("", "noop"):
            tg_answer_callback_query(cq_id, "Уже обработано ✅")
            return {"ok": True}

        parts = data_raw.split("|", 1)
        if len(parts) != 2:
            tg_answer_callback_query(cq_id, "Некорректные данные кнопки")
            return {"ok": True}

        action, order_id = parts[0], parts[1]

        # убираем клавиатуру
        try:
            tg_edit_reply_markup(chat_id=chat["id"], message_id=mid, reply_markup=None)
        except Exception as e:
            print("remove kb error:", e)

        # теперь действия
        if action == "received":
            sheet_update_status(order_id, "Доставлено")
            footer = f"\n\n📌 <i>ТМЦ получено — отметил: {who}</i>"
            tg_edit_message_text(chat_id=chat["id"], message_id=mid,
                                 new_text=(orig_text or "").rstrip() + footer)
            tg_answer_callback_query(cq_id, "Отмечено: ТМЦ получено 📦")

        elif action == "approve":
            sheet_update_status(order_id, "В РАБОТУ: СОГЛАСОВАНО")
            footer = f"\n\n📌 <i>В РАБОТУ — подтвердил: {who}</i>"
            tg_edit_message_text(chat_id=chat["id"], message_id=mid,
                                 new_text=(orig_text or "").rstrip() + footer)
            tg_answer_callback_query(cq_id, "Отмечено: В РАБОТУ ✅")

        elif action == "reject":
            sheet_update_status(order_id, "ОТКЛОНЕНО")
            footer = f"\n\n📌 <i>ОТКЛОНЕНО — отметил: {who}</i>"
            tg_edit_message_text(chat_id=chat["id"], message_id=mid,
                                 new_text=(orig_text or "").rstrip() + footer)
            tg_answer_callback_query(cq_id, "Отмечено: ОТКЛОНЕНО ❌")

        elif action == "revise":
            PENDING_REVISE[user.get("id")] = order_id
            footer = f"\n\n📌 <i>НА ДОРАБОТКУ — ждём комментарий от: {who}</i>"
            tg_edit_message_text(chat_id=chat["id"], message_id=mid,
                                 new_text=(orig_text or "").rstrip() + footer)
            tg_answer_callback_query(cq_id, "Пришлите комментарий одним сообщением 🔧")

        else:
            tg_answer_callback_query(cq_id, "Неизвестное действие")

        return {"ok": True}

    # --- текстовое сообщение (для НА ДОРАБОТКУ) ---
    if "message" in upd:
        msg  = upd["message"]
        uid  = msg.get("from", {}).get("id")
        text = (msg.get("text") or "").strip()
        if uid in PENDING_REVISE and text:
            order_id = PENDING_REVISE.pop(uid)
            sheet_update_status(order_id, "На доработку", comment=text)
        return {"ok": True}

    return {"ok": True}
