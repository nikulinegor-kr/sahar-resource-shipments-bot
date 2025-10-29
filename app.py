import os, html, re, requests
from fastapi import FastAPI, Request, Header, HTTPException
from typing import Optional, Dict, Any

# ===== НАСТРОЙКИ =====
BOT_TOKEN        = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID          = os.getenv("CHAT_ID", "").strip()              # ID TG-группы, формат -100...
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "").strip()       # тот же токен, что и в Google Script
SHEET_SCRIPT_URL = os.getenv("SHEET_SCRIPT_URL", "").strip()     # URL Web App из Apps Script
TG_API           = f"https://api.telegram.org/bot{BOT_TOKEN}"

app = FastAPI(title="Snab Orders Bot")

# ===== ВРЕМЕННОЕ ХРАНИЛИЩЕ =====
WAITING_COMMENT: Dict[int, str] = {}  # user_id -> order_id

# ===== УТИЛИТЫ =====
def tg(method: str, data: Dict[str, Any]):
    try:
        r = requests.post(f"{TG_API}/{method}", json=data, timeout=15)
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}

def tg_message(text, buttons=None):
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    return tg("sendMessage", payload)

def sheet_update(payload):
    if not SHEET_SCRIPT_URL:
        return {"ok": False, "error": "SHEET_SCRIPT_URL not set"}
    try:
        r = requests.post(SHEET_SCRIPT_URL, json=payload, timeout=15)
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}

def normalize(s): return re.sub(r"\s+", " ", s.lower().replace("\u00a0", " ")).strip()

# ====== ВСПОМОГАТЕЛЬНЫЕ ======
def render(data):
    g = lambda k: html.escape(str(data.get(k, "") or ""))
    msg = f"""📦 <b>Уведомление о заявке</b>
🧾 <b>Заявка:</b> {g('order_id')}
⭐ <b>Приоритет:</b> {g('priority')}
🚚 <b>Статус:</b> {g('status')}
👤 <b>Заявитель:</b> {g('applicant')}
📝 <b>Комментарий:</b> {g('comment')}
"""
    return msg

def build_keyboard(data):
    rows = []
    st = normalize(data.get("status", ""))
    cm = normalize(data.get("comment", ""))
    order = data.get("order_id")

    if "доставлено" in st and "тк" in st:
        rows.append([{"text": "✅ ТМЦ ПОЛУЧЕНО", "callback_data": f"recv|{order}"}])

    if "требуется согласование" in cm:
        rows.append([{"text": "🟩 В РАБОТУ: СОГЛАСОВАНО", "callback_data": f"approve|{order}"}])
        rows.append([
            {"text": "🟨 На доработку", "callback_data": f"revise|{order}"},
            {"text": "🟥 ОТКЛОНЕНО", "callback_data": f"reject|{order}"}
        ])
    return rows or None

# ====== API ======
@app.get("/health")
def health(): return {"ok": True}

@app.post("/notify")
async def notify(req: Request, authorization: Optional[str] = Header(None)):
    if not authorization or authorization.replace("Bearer ", "").strip() != WEBHOOK_SECRET:
        raise HTTPException(401, "Unauthorized")

    data = await req.json()
    text = render(data)
    keyboard = build_keyboard(data)
    tg_message(text, keyboard)
    return {"ok": True}

@app.post("/tg")
async def tg_webhook(req: Request):
    upd = await req.json()
    print("TG update:", upd)

    # ==== CALLBACK-КНОПКИ ====
    if "callback_query" in upd:
        cq = upd["callback_query"]
        user = cq.get("from", {})
        user_id = user.get("id")
        data = cq.get("data", "")
        msg = cq["message"]
        cid, mid = msg["chat"]["id"], msg["message_id"]

        def answer(text): tg("answerCallbackQuery", {"callback_query_id": cq["id"], "text": text})

        if "|" in data:
            act, order = data.split("|", 1)
            if act == "recv":
                sheet_update({"action": "status", "order_id": order, "new_status": "Доставлено"})
                tg("editMessageReplyMarkup", {"chat_id": cid, "message_id": mid,
                                              "reply_markup": {"inline_keyboard": [[{"text": "✅ Отмечено", "callback_data": "noop"}]]}})
                return answer("Отмечено")

            if act == "approve":
                sheet_update({"action": "status", "order_id": order, "new_status": "В РАБОТУ: СОГЛАСОВАНО"})
                tg("editMessageReplyMarkup", {"chat_id": cid, "message_id": mid,
                                              "reply_markup": {"inline_keyboard": [[{"text": "🟩 Согласовано", "callback_data": "noop"}]]}})
                return answer("Согласовано")

            if act == "reject":
                sheet_update({"action": "status", "order_id": order, "new_status": "ОТКЛОНЕНО"})
                tg("editMessageReplyMarkup", {"chat_id": cid, "message_id": mid,
                                              "reply_markup": {"inline_keyboard": [[{"text": "🟥 Отклонено", "callback_data": "noop"}]]}})
                return answer("Отклонено")

            if act == "revise":
                WAITING_COMMENT[user_id] = order
                tg_message(f"🟨 Введите комментарий для заявки <b>{order}</b>")
                return answer("Введите комментарий")

    # ==== СООБЩЕНИЕ ОТ ПОЛЬЗОВАТЕЛЯ ====
    if "message" in upd:
        msg = upd["message"]
        user_id = msg["from"]["id"]
        text = msg.get("text", "").strip()
        if user_id in WAITING_COMMENT:
            order = WAITING_COMMENT.pop(user_id)
            sheet_update({"action": "status_with_comment", "order_id": order,
                          "new_status": "На доработку", "comment": text})
            tg_message(f"🟨 Заявка <b>{order}</b> отправлена на доработку.\nКомментарий: {html.escape(text)}")

    return {"ok": True}
