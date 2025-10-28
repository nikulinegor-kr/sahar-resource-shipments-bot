from fastapi import FastAPI, Request, Header, HTTPException
from typing import Optional, Dict, Any
import requests
from threading import Thread
import os

app = FastAPI()

# ===== НАСТРОЙКИ =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
SHEET_SCRIPT_URL = os.getenv("SHEET_SCRIPT_URL")
SHEET_API_KEY = os.getenv("SHEET_API_KEY")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

pending_comments = {}  # сюда сохраняем заявки "на доработку"


# ======= УТИЛИТЫ =======

def tg_send_message(text: str, reply_markup: Optional[Dict] = None, parse_mode="HTML"):
    """Отправка сообщения в Telegram"""
    if not BOT_TOKEN or not CHAT_ID:
        print("⚠️ BOT_TOKEN or CHAT_ID missing")
        return

    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        requests.post(f"{TG_API}/sendMessage", json=payload, timeout=8)
    except Exception as e:
        print("tg_send_message error:", e)


def update_sheet_status(order_id: str, new_status: str):
    """Отправляем в Google Apps Script обновление статуса"""
    if not SHEET_SCRIPT_URL or not SHEET_API_KEY:
        print("⚠️ SHEET_SCRIPT_URL or SHEET_API_KEY missing")
        return

    try:
        res = requests.post(
            SHEET_SCRIPT_URL,
            headers={"Authorization": f"Bearer {SHEET_API_KEY}"},
            json={"action": "update_status", "order_id": order_id, "new_status": new_status},
            timeout=10,
        )
        print("Sheet update:", res.status_code, res.text)
    except Exception as e:
        print("update_sheet_status error:", e)


def build_keyboard(comment: str, order_id: str):
    """Выбираем клавиатуру по типу комментария"""
    c = comment.lower().strip()
    if "требуется согласование" in c:
        return {
            "inline_keyboard": [
                [
                    {"text": "✅ В РАБОТУ", "callback_data": f"approve|{order_id}"},
                    {"text": "🔧 НА ДОРАБОТКУ", "callback_data": f"revise|{order_id}"},
                    {"text": "❌ ОТКЛОНЕНО", "callback_data": f"reject|{order_id}"},
                ]
            ]
        }
    elif "доставлено в тк" in c:
        return {
            "inline_keyboard": [
                [{"text": "📦 ТМЦ ПОЛУЧЕНО", "callback_data": f"received|{order_id}"}]
            ]
        }
    else:
        return None


# ======= /notify (из Google Apps Script) =======

@app.post("/notify")
async def notify(req: Request, authorization: Optional[str] = Header(None)):
    if authorization != f"Bearer {WEBHOOK_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    data = await req.json()
    order_id = data.get("order_id")
    comment = data.get("comment", "")
    status = data.get("status", "")

    msg = (
        f"📦 <b>Уведомление о заявке</b>\n"
        f"🧾 <b>Заявка:</b> {order_id}\n"
        f"⭐ <b>Статус:</b> {status}\n"
        f"📝 <b>Комментарий:</b> {comment}"
    )

    keyboard = build_keyboard(comment, order_id)

    Thread(target=lambda: tg_send_message(msg, reply_markup=keyboard)).start()
    return {"ok": True}


# ======= /tg — обработчик нажатий =======

@app.post("/tg")
async def tg_webhook(req: Request):
    update = await req.json()
    print("TG update:", update)

    if "callback_query" in update:
        cq = update["callback_query"]
        data = cq.get("data", "")
        user = cq.get("from", {})
        user_id = user.get("id")
        chat_id = cq["message"]["chat"]["id"]

        parts = data.split("|")
        if len(parts) != 2:
            return {"ok": False}

        action, order_id = parts

        if action == "approve":
            new_status = "В РАБОТУ: СОГЛАСОВАНО"
            msg = f"✅ Заявка <b>{order_id}</b> согласована и принята в работу."
            update_sheet_status(order_id, new_status)
            tg_send_message(msg)

        elif action == "reject":
            new_status = "ОТКЛОНЕНО"
            msg = f"❌ Заявка <b>{order_id}</b> отклонена."
            update_sheet_status(order_id, new_status)
            tg_send_message(msg)

        elif action == "revise":
            pending_comments[user_id] = order_id
            tg_send_message(
                f"🔧 Для заявки <b>{order_id}</b> требуется уточнение.\n"
                f"Пожалуйста, ответьте сюда сообщением — ваш комментарий будет добавлен в таблицу."
            )

        elif action == "received":
            new_status = "ТМЦ ПОЛУЧЕНО"
            msg = f"📦 Заявка <b>{order_id}</b> отмечена как полученная."
            update_sheet_status(order_id, new_status)
            tg_send_message(msg)

        return {"ok": True}

    # === пользователь отвечает сообщением (доработка) ===
    if "message" in update:
        msg = update["message"]
        user = msg.get("from", {})
        user_id = user.get("id")
        text = msg.get("text", "").strip()

        if user_id in pending_comments:
            order_id = pending_comments.pop(user_id)
            new_status = f"На доработку: {text}"
            update_sheet_status(order_id, new_status)
            tg_send_message(
                f"🔧 Заявка <b>{order_id}</b> отправлена на доработку с комментарием:\n{text}"
            )

        return {"ok": True}

    return {"ok": False}


# ======= HEALTH =======
@app.get("/health")
def health():
    return {"status": "ok"}
