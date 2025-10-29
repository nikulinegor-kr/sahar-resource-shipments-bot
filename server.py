from fastapi import FastAPI, Request, Header, HTTPException
from typing import Optional, Dict, Any
import requests
from threading import Thread
import os
import re
import html

app = FastAPI()

# ===== НАСТРОЙКИ =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
SHEET_SCRIPT_URL = os.getenv("SHEET_SCRIPT_URL")
SHEET_API_KEY = os.getenv("SHEET_API_KEY")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

pending_comments: Dict[int, str] = {}  # кто оставляет комментарий для "На доработку"


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


def _norm(s: str) -> str:
    """Нормализуем строку: нижний регистр, NBSP -> пробел, схлопываем пробелы."""
    return re.sub(r"\s+", " ", (s or "").replace("\u00A0", " ").lower()).strip()


def _is_delivered_to_tk(status: str) -> bool:
    """Проверяем разные варианты 'доставлено в тк' в статусе."""
    n = _norm(status)
    if not n:
        return False
    # прямое совпадение
    if n == "доставлено в тк":
        return True
    # допуски: 'доставлено в т.к.' / двойные пробелы / знаки
    return ("доставлено" in n) and (re.search(r"\bв\s*т\.?к\.?\b", n) is not None or " в тк" in n)


def build_keyboard(comment: str, status: str, order_id: str) -> Optional[Dict]:
    """
    Кнопки в СТОЛБЕЦ (каждая на своей строке):
      – если СТАТУС = 'доставлено в тк' → '📦 ТМЦ ПОЛУЧЕНО'
      – если КОММЕНТАРИЙ содержит 'требуется согласование' → три кнопки ниже, каждая на своей строке
    """
    rows = []

    # 1) По статусу — кнопка получения ТМЦ (одна строка)
    if _is_delivered_to_tk(status):
        rows.append([{"text": "📦 ТМЦ ПОЛУЧЕНО", "callback_data": f"received|{order_id}"}])

    # 2) По комментарию — набор согласования (каждая кнопка на отдельной строке)
    c = _norm(comment)
    if "требуется согласование" in c:
        rows.append([{"text": "✅ В РАБОТУ",      "callback_data": f"approve|{order_id}"}])
        rows.append([{"text": "🔧 НА ДОРАБОТКУ",  "callback_data": f"revise|{order_id}"}])
        rows.append([{"text": "❌ ОТКЛОНЕНО",     "callback_data": f"reject|{order_id}"}])

    return {"inline_keyboard": rows} if rows else None


def render_message(data: Dict[str, Any]) -> str:
    g = lambda k: html.escape(str(data.get(k) or ""))
    parts = [
        "📦 <b>Уведомление о заявке</b>",
        f"🧾 <b>Заявка:</b> {g('order_id')}",
        f"⭐ <b>Статус:</b> {g('status')}",
    ]
    if data.get("priority"):
        parts.append(f"🏷 <b>Приоритет:</b> {g('priority')}")
    if data.get("applicant"):
        parts.append(f"👤 <b>Заявитель:</b> {g('applicant')}")
    if data.get("comment"):
        parts.append(f"📝 <b>Комментарий:</b> {g('comment')}")
    return "\n".join(parts)


# ======= /notify (из Google Apps Script) =======

@app.post("/notify")
async def notify(req: Request, authorization: Optional[str] = Header(None)):
    if authorization != f"Bearer {WEBHOOK_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    data = await req.json()
    order_id = data.get("order_id", "")
    comment  = data.get("comment", "")
    status   = data.get("status", "")

    # соберём текст и клавиатуру
    msg = render_message(data)
    keyboard = build_keyboard(comment, status, order_id)

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
        first_name = user.get("first_name", "")
        last_name = user.get("last_name", "")
        user_name = (first_name + " " + last_name).strip() or f"ID:{user_id}"
        chat_id = cq["message"]["chat"]["id"]

        parts = data.split("|", 1)
        if len(parts) != 2:
            return {"ok": False}

        action, order_id = parts

        # ✅ В РАБОТУ
        if action == "approve":
            new_status = "В РАБОТУ: СОГЛАСОВАНО"
            msg = f"✅ Заявка <b>{order_id}</b> согласована и принята в работу.\n👤 Исполнитель: <b>{html.escape(user_name)}</b>"
            update_sheet_status(order_id, new_status)
            tg_send_message(msg)

        # ❌ ОТКЛОНЕНО
        elif action == "reject":
            new_status = "ОТКЛОНЕНО"
            msg = f"❌ Заявка <b>{order_id}</b> отклонена.\n👤 Исполнитель: <b>{html.escape(user_name)}</b>"
            update_sheet_status(order_id, new_status)
            tg_send_message(msg)

        # 🔧 НА ДОРАБОТКУ
        elif action == "revise":
            pending_comments[user_id] = order_id
            tg_send_message(
                f"🔧 Для заявки <b>{order_id}</b> требуется уточнение.\n"
                f"Пожалуйста, ответьте сюда сообщением — ваш комментарий будет добавлен в таблицу.\n"
                f"👤 Исполнитель: <b>{html.escape(user_name)}</b>"
            )

        # 📦 ТМЦ ПОЛУЧЕНО
        elif action == "received":
            new_status = "ТМЦ ПОЛУЧЕНО"
            msg = f"📦 Заявка <b>{order_id}</b> отмечена как полученная.\n👤 Ответственный: <b>{html.escape(user_name)}</b>"
            update_sheet_status(order_id, new_status)
            tg_send_message(msg)

        return {"ok": True}

    # === пользователь отвечает сообщением (доработка) ===
    if "message" in update:
        msg = update["message"]
        user = msg.get("from", {})
        user_id = user.get("id")
        text = msg.get("text", "").strip()
        first_name = user.get("first_name", "")
        last_name = user.get("last_name", "")
        user_name = (first_name + " " + last_name).strip() or f"ID:{user_id}"

        if user_id in pending_comments:
            order_id = pending_comments.pop(user_id)
            new_status = f"На доработку: {text}"
            update_sheet_status(order_id, new_status)
            tg_send_message(
                f"🔧 Заявка <b>{order_id}</b> отправлена на доработку с комментарием:\n"
                f"{html.escape(text)}\n👤 Исполнитель: <b>{html.escape(user_name)}</b>"
            )

        return {"ok": True}

    return {"ok": False}
    # === пользователь отвечает сообщением (доработка) ===
    if "message" in update:
        msg = update["message"]
        user = msg.get("from", {})
        user_id = user.get("id")
        text = msg.get("text", "").strip()

        if user_id in pending_comments:
            order_id = pending_comments.pop(user_id)
            # фиксируем комментарий в статусе — можно сменить на отдельную запись в колонку Комментарий,
            # если в Apps Script сделана соответствующая обработка
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
