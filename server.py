import os
import requests
import html
from typing import Optional, Dict, Any
from fastapi import FastAPI, Request, Header, HTTPException

app = FastAPI(title="SnabOrders Bot", version="2.7")

# ===== ENV =====
BOT_TOKEN        = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID          = os.getenv("CHAT_ID", "").strip()          # -100... (id группы)
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "").strip()   # общий секрет для /notify
SHEET_SCRIPT_URL = os.getenv("SHEET_SCRIPT_URL", "").strip() # URL веб-приложения Apps Script
SHEET_API_KEY    = os.getenv("SHEET_API_KEY", "").strip()    # ключ для вызова Apps Script

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# user_id -> order_id  (для режима «НА ДОРАБОТКУ»)
PENDING_REVISE: Dict[int, str] = {}

# ---------- ВСПОМОГАТЕЛЬНЫЕ ----------

def get_str(data: Dict[str, Any], key: str) -> str:
    """Безопасно достаём поле как строку (чтобы не падало из-за типов)."""
    v = data.get(key)
    if v is None:
        return ""
    try:
        return str(v).strip()
    except Exception:
        return ""

def norm(s: str) -> str:
    return str(s or "").lower().replace("\u00a0", " ").strip()

# ---------- TG УТИЛИТЫ ----------

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
    try:
        r = requests.post(f"{TG_API}/sendMessage", json=payload, timeout=10)
        print("tg_send_message:", r.status_code, r.text[:200])
    except Exception as e:
        print("tg_send_message error:", e)

def tg_edit_reply_markup(chat_id: int, message_id: int, reply_markup: Optional[Dict]):
    try:
        r = requests.post(
            f"{TG_API}/editMessageReplyMarkup",
            json={"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup},
            timeout=10
        )
        print("tg_edit_reply_markup:", r.status_code, r.text[:200])
    except Exception as e:
        print("tg_edit_reply_markup error:", e)

def tg_edit_message_text(chat_id: int, message_id: int, new_text: str, parse_mode: str="HTML"):
    """Редактируем текст исходного сообщения (без нового поста в чат)."""
    try:
        r = requests.post(
            f"{TG_API}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": new_text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True
            },
            timeout=10
        )
        print("tg_edit_message_text:", r.status_code, r.text[:200])
    except Exception as e:
        print("tg_edit_message_text error:", e)

def tg_answer_callback_query(callback_query_id: str, text: str = "", show_alert: bool = False):
    """Показывает всплывающий toast только для нажавшего на кнопку."""
    try:
        requests.post(
            f"{TG_API}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text, "show_alert": show_alert},
            timeout=8,
        )
    except Exception as e:
        print("tg_answer_callback_query error:", e)

def fmt_user(u: Dict[str, Any]) -> str:
    uname = u.get("username")
    if uname:
        return f"@{uname}"
    first = (u.get("first_name") or "").strip()
    last  = (u.get("last_name")  or "").strip()
    full = (first + " " + last).strip()
    return html.escape(full) if full else f"id:{u.get('id')}"

# ---------- SHEET УТИЛИТЫ ----------

def sheet_update_status(order_id: str, new_status: str, comment: Optional[str]=None):
    if not SHEET_SCRIPT_URL or not SHEET_API_KEY:
        print("SHEET: missing SHEET_SCRIPT_URL/SHEET_API_KEY")
        return {"ok": False, "error": "config"}
    payload = {"action": "update_status", "order_id": order_id, "new_status": new_status}
    if comment is not None:
        payload["comment"] = comment
        payload["action"] = "status_with_comment"
    try:
        r = requests.post(
            SHEET_SCRIPT_URL,
            headers={"Authorization": f"Bearer {SHEET_API_KEY}"},
            json=payload,
            timeout=12
        )
        print("sheet_update_status:", r.status_code, r.text[:200])
        if r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        return {"ok": r.ok}
    except Exception as e:
        print("sheet_update_status error:", e)
        return {"ok": False, "error": str(e)}

# ---------- ТЕКСТ СООБЩЕНИЯ ----------

def make_message(data: Dict[str, Any]) -> str:
    order_id  = get_str(data, "order_id")
    priority  = get_str(data, "priority")
    status    = get_str(data, "status")
    carrier   = get_str(data, "carrier")
    ttn       = get_str(data, "ttn")
    ship_date = get_str(data, "ship_date")
    arrival   = get_str(data, "arrival")
    applicant = get_str(data, "applicant")
    comment   = get_str(data, "comment")
    invoice   = get_str(data, "invoice")  # поле «Счёт/КП» (URL)

    lines = ["📦 <b>Уведомление о заявке</b>"]

    if order_id:
        lines.append(f"🧾 <b>Заявка:</b> {html.escape(order_id)}")
    if priority:
        lines.append(f"⭐ <b>Приоритет:</b> {html.escape(priority)}")
    if status:
        lines.append(f"🚚 <b>Статус:</b> {html.escape(status)}")
    if carrier:
        lines.append(f"🚛 <b>ТК:</b> {html.escape(carrier)}")
    if ttn:
        lines.append(f"📄 <b>№ ТТН:</b> {html.escape(ttn)}")
    if ship_date:
        lines.append(f"📅 <b>Дата отгрузки:</b> {html.escape(ship_date)}")
    if arrival:
        lines.append(f"📅 <b>Дата прибытия:</b> {html.escape(arrival)}")
    if applicant:
        lines.append(f"👤 <b>Заявитель:</b> {html.escape(applicant)}")
    if comment:
        lines.append(f"📝 <b>Комментарий:</b> {html.escape(comment)}")
    if invoice:
        lines.append("📄 <b>Счёт/КП:</b> доступен по кнопке ниже")

    return "\n".join(lines)

# ---------- КЛАВИАТУРА ----------

def build_keyboard(data: Dict[str, Any]) -> Optional[Dict]:
    rows = []

    order_id = get_str(data, "order_id")
    status   = norm(get_str(data, "status"))
    comment  = norm(get_str(data, "comment"))
    invoice  = get_str(data, "invoice")

    # Кнопка "ТМЦ ПОЛУЧЕНО" при статусе "доставлено в тк"
    if order_id and "доставлено в тк" in status:
        rows.append([
            {"text": "📦 ТМЦ ПОЛУЧЕНО", "callback_data": f"received|{order_id}"}
        ])

    # Кнопки согласования при "ТРЕБУЕТСЯ СОГЛАСОВАНИЕ" в комментарии
    if order_id and "требуется согласование" in comment:
        rows.append([{"text": "✅ В РАБОТУ",     "callback_data": f"approve|{order_id}"}])
        rows.append([{"text": "🔧 НА ДОРАБОТКУ", "callback_data": f"revise|{order_id}"}])
        rows.append([{"text": "❌ ОТКЛОНЕНО",    "callback_data": f"reject|{order_id}"}])

    # Кнопка "Открыть счёт" (url) — только если это нормальная ссылка
    if invoice and (invoice.startswith("http://") or invoice.startswith("https://")):
        rows.append([
            {"text": "📄 Открыть счёт", "url": invoice}
        ])

    if not rows:
        return None
    return {"inline_keyboard": rows}

# ---------- ROUTES ----------

@app.get("/")
def root():
    return {"ok": True, "routes": ["/", "/health", "/notify", "/tg"]}

@app.get("/health")
def health():
    return {"ok": True, "service": "snaborders-bot"}

# Прилетает из Google Apps Script при изменении строки
@app.post("/notify")
async def notify(req: Request, authorization: Optional[str] = Header(None)):
    if authorization != f"Bearer {WEBHOOK_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    data = await req.json()
    print("NOTIFY data:", data)

    text = make_message(data)
    kb   = build_keyboard(data)
    tg_send_message(text, reply_markup=kb)
    return {"ok": True}

# Telegram webhook
@app.post("/tg")
async def tg_webhook(req: Request):
    upd = await req.json()
    print("TG update:", str(upd)[:800])

    # --- нажатие инлайн-кнопки ---
    if "callback_query" in upd:
        cq        = upd["callback_query"]
        cq_id     = cq.get("id")
        user      = cq.get("from", {}) or {}
        chat      = cq.get("message", {}).get("chat", {}) or {}
        mid       = cq.get("message", {}).get("message_id")
        orig_text = cq.get("message", {}).get("text") or ""
        data_raw  = (cq.get("data") or "")
        parts     = data_raw.split("|", 1)
        who       = fmt_user(user)

        if data_raw in ("", "noop"):
            tg_answer_callback_query(cq_id, "Уже отмечено ✅")
            return {"ok": True}

        if len(parts) != 2:
            tg_answer_callback_query(cq_id, "Некорректные данные кнопки")
            return {"ok": True}

        action, order_id = parts[0], parts[1]

        try:
            tg_edit_reply_markup(chat_id=chat["id"], message_id=mid, reply_markup=None)

            if action == "received":
                sheet_update_status(order_id, "Доставлено")
                footer = f"\n\n📌 <i>ТМЦ получено — отметил: {who}</i>"
                new_text = (orig_text or "").rstrip() + footer
                tg_edit_message_text(chat["id"], mid, new_text)
                tg_answer_callback_query(cq_id, "Отмечено как получено 📦")
                return {"ok": True}

            elif action == "approve":
                sheet_update_status(order_id, "В РАБОТУ: СОГЛАСОВАНО")
                footer = f"\n\n📌 <i>В РАБОТУ — подтвердил: {who}</i>"
                new_text = (orig_text or "").rstrip() + footer
                tg_edit_message_text(chat["id"], mid, new_text)
                tg_answer_callback_query(cq_id, "Отмечено: В РАБОТУ ✅")
                return {"ok": True}

            elif action == "reject":
                sheet_update_status(order_id, "ОТКЛОНЕНО")
                footer = f"\n\n📌 <i>ОТКЛОНЕНО — отметил: {who}</i>"
                new_text = (orig_text or "").rstrip() + footer
                tg_edit_message_text(chat["id"], mid, new_text)
                tg_answer_callback_query(cq_id, "Отмечено: ОТКЛОНЕНО")
                return {"ok": True}

            elif action == "revise":
                PENDING_REVISE[user.get("id")] = order_id
                footer = f"\n\n📌 <i>НА ДОРАБОТКУ — ждём комментарий от: {who}</i>"
                new_text = (orig_text or "").rstrip() + footer
                tg_edit_message_text(chat["id"], mid, new_text)
                tg_answer_callback_query(cq_id, "Пришлите одним сообщением комментарий 🔧")
                return {"ok": True}

            else:
                tg_answer_callback_query(cq_id, "Неизвестное действие")
                return {"ok": True}

        except Exception as e:
            print("callback handler error:", e)
            tg_answer_callback_query(cq_id, "Ошибка обработки нажатия")
            return {"ok": True}

    # --- текст от пользователя (для «НА ДОРАБОТКУ») ---
    if "message" in upd:
        msg  = upd["message"]
        uid  = msg.get("from", {}).get("id")
        text = (msg.get("text") or "").strip()
        if uid in PENDING_REVISE and text:
            order_id = PENDING_REVISE.pop(uid)
            sheet_update_status(order_id, "На доработку", comment=text)
        return {"ok": True}

    return {"ok": True}
