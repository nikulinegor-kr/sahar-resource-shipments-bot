import os
import html
import json
import requests
from datetime import datetime
from fastapi import FastAPI, Request
from typing import Dict, Any, Optional

BOT_TOKEN = os.getenv("BOT_TOKEN_NEW", "").strip()
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""
SHEET_SCRIPT_URL = os.getenv("SHEET_SCRIPT_URL", "").strip()
SHEET_SECRET = os.getenv("SHEET_SECRET", "sahar2025secure_longtoken").strip()
SERVICE_NAME = os.getenv("SERVICE_NAME", "snab-neworder").strip()

app = FastAPI(title="Snab NewOrder Service", version="1.0.0")

# Простейшее хранение состояния (переживает только в рамках процесса)
STATE: Dict[int, Dict[str, Any]] = {}

PRIORITIES = ["Аварийно", "Приоритетно", "Планово"]

def tg_call(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not BOT_TOKEN:
        return {"ok": False, "error": "BOT_TOKEN_NEW missing"}
    url = f"{TG_API}/{method}"
    r = requests.post(url, json=payload, timeout=15)
    try:
        return r.json()
    except Exception:
        return {"ok": False, "error": f"bad json: {r.text}"}

def send_message(chat_id: int, text: str, reply_markup: Optional[Dict[str, Any]] = None):
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if reply_markup:
        data["reply_markup"] = reply_markup
    return tg_call("sendMessage", data)

def edit_message_reply_markup(chat_id: int, message_id: int, reply_markup: Optional[Dict[str, Any]] = None):
    data = {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup or {"inline_keyboard": []}}
    return tg_call("editMessageReplyMarkup", data)

def answer_callback_query(cb_id: str, text: str = "", show_alert: bool = False):
    data = {"callback_query_id": cb_id}
    if text:
        data["text"] = text
    if show_alert:
        data["show_alert"] = True
    return tg_call("answerCallbackQuery", data)

def kb_priorities():
    return {
        "inline_keyboard": [[{"text": p, "callback_data": f"prio:{p}"}] for p in PRIORITIES]
    }

def kb_confirm():
    return {
        "inline_keyboard": [
            [{"text": "✅ Подтвердить", "callback_data": "confirm"}],
            [{"text": "❌ Отмена", "callback_data": "cancel"}],
        ]
    }

def kb_skip():
    return {
        "inline_keyboard": [
            [{"text": "Пропустить", "callback_data": "skip"}]
        ]
    }

def preview_text(d: Dict[str, Any]) -> str:
    lines = ["📥 <b>Заявка (предпросмотр)</b>"]
    def add(label, key):
        val = (d.get(key) or "").strip()
        if val:
            lines.append(f"{label}: {html.escape(val)}")
    add("📅 Дата", "date")
    add("🧾 Заявка", "title")
    add("⭐ Приоритет", "priority")
    add("🔢 Кол-во", "qty")
    add("🖼 Фото (file_id/url)", "photo")
    add("🔧 № ЗЧ", "part_no")
    add("🚘 VIN / СОР", "vin_or_sor")
    add("📝 Комментарий", "comment")
    add("👤 Заявитель", "applicant")
    return "\n".join(lines)

def post_to_sheet(d: Dict[str, Any]) -> bool:
    # Отправляем в GAS Web App
    payload = dict(d)
    payload["secret"] = SHEET_SECRET
    headers = {"X-Auth": SHEET_SECRET, "Content-Type": "application/json"}
    r = requests.post(SHEET_SCRIPT_URL, headers=headers, data=json.dumps(payload), timeout=20)
    return r.ok

@app.get("/")
def root():
    return {"ok": True, "service": SERVICE_NAME, "routes": ["/", "/health", "/tg (GET/POST)"]}

@app.get("/health")
def health():
    return {"ok": True, "service": SERVICE_NAME}

@app.get("/tg")
def tg_get():
    return {"ok": True, "route": "/tg"}

@app.post("/tg")
async def tg_post(req: Request):
    update = await req.json()
    # print(update)  # можно включить лог
    if "message" in update:
        return await handle_message(update["message"])
    if "callback_query" in update:
        return await handle_callback(update["callback_query"])
    return {"ok": True}

async def handle_message(msg: Dict[str, Any]):
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()

    # старт команды
    if text.startswith("/start") or text.startswith("/help"):
        send_message(chat_id, "Команда для новой заявки: /neworder")
        return {"ok": True}

    if text.startswith("/neworder"):
        # Инициализируем состояние
        STATE[chat_id] = {
            "step": "title",
            "date": datetime.now().strftime("%d.%m.%Y"),
            "title": "",
            "priority": "",
            "qty": "",
            "photo": "",
            "part_no": "",
            "vin_or_sor": "",
            "comment": "",
            "applicant": f"{msg.get('from', {}).get('first_name','')}".strip()
        }
        send_message(chat_id, "Введите <b>название заявки</b> (что требуется):")
        return {"ok": True}

    # Продолжаем диалог по состоянию
    st = STATE.get(chat_id)
    if not st:
        # игнор сообщений вне сценария
        return {"ok": True}

    step = st.get("step")

    if step == "title":
        st["title"] = text
        st["step"] = "priority"
        send_message(chat_id, "Выберите <b>приоритет</b>:", reply_markup=kb_priorities())
        return {"ok": True}

    if step == "qty":
        st["qty"] = text
        st["step"] = "photo"
        send_message(chat_id, "Пришлите <b>фото</b> (или нажмите «Пропустить»)", reply_markup=kb_skip())
        return {"ok": True}

    if step == "part_no":
        st["part_no"] = text
        st["step"] = "vin"
        send_message(chat_id, "Укажи <b>VIN / СОР</b> (или «Пропустить»):", reply_markup=kb_skip())
        return {"ok": True}

    if step == "vin":
        st["vin_or_sor"] = text
        st["step"] = "comment"
        send_message(chat_id, "Добавь <b>комментарий</b> (или «Пропустить»):", reply_markup=kb_skip())
        return {"ok": True}

    if step == "comment":
        st["comment"] = text
        st["step"] = "confirm"
        send_message(chat_id, preview_text(st), reply_markup=kb_confirm())
        return {"ok": True}

    # Если присылают фото на соответствующем шаге
    if step == "photo" and "photo" in msg:
        # сохраняем найбольший размер как file_id
        sizes = msg["photo"]
        if isinstance(sizes, list) and sizes:
            st["photo"] = sizes[-1]["file_id"]
        st["step"] = "part_no"
        send_message(chat_id, "Укажи <b>№ запасной части</b> (или «Пропустить»):", reply_markup=kb_skip())
        return {"ok": True}

    # Если пришёл текст на шаге photo и это не «пропустить», считаем как ссылку/описание
    if step == "photo" and text:
        st["photo"] = text
        st["step"] = "part_no"
        send_message(chat_id, "Укажи <b>№ запасной части</b> (или «Пропустить»):", reply_markup=kb_skip())
        return {"ok": True}

    return {"ok": True}

async def handle_callback(cb: Dict[str, Any]):
    cb_id = cb.get("id")
    from_user = cb.get("from", {})
    msg = cb.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    message_id = msg.get("message_id")
    data = cb.get("data", "")

    st = STATE.get(chat_id)
    if not st:
        answer_callback_query(cb_id, "Сессия не найдена. Нажмите /neworder")
        return {"ok": True}

    if data.startswith("prio:"):
        st["priority"] = data.split("prio:", 1)[-1]
        st["step"] = "qty"
        # убрать клавиатуру выбора приоритета
        edit_message_reply_markup(chat_id, message_id, {"inline_keyboard": []})
        send_message(chat_id, "Укажи <b>количество</b> (цифрой):")
        answer_callback_query(cb_id)
        return {"ok": True}

    if data == "skip":
        # определить, на каком шаге были
        step = st.get("step")
        edit_message_reply_markup(chat_id, message_id, {"inline_keyboard": []})

        if step == "photo":
            st["photo"] = ""
            st["step"] = "part_no"
            send_message(chat_id, "Укажи <b>№ запасной части</b> (или «Пропустить»):", reply_markup=kb_skip())
        elif step == "vin":
            st["vin_or_sor"] = ""
            st["step"] = "comment"
            send_message(chat_id, "Добавь <b>комментарий</b> (или «Пропустить»):", reply_markup=kb_skip())
        elif step == "comment":
            st["comment"] = ""
            st["step"] = "confirm"
            send_message(chat_id, preview_text(st), reply_markup=kb_confirm())
        else:
            send_message(chat_id, "Ок, пропустил.")
        answer_callback_query(cb_id)
        return {"ok": True}

    if data == "confirm":
        edit_message_reply_markup(chat_id, message_id, {"inline_keyboard": []})
        # Пакуем и шлём в Таблицу
        payload = {
            "date": st.get("date"),
            "title": st.get("title"),
            "priority": st.get("priority"),
            "qty": st.get("qty"),
            "photo": st.get("photo"),
            "part_no": st.get("part_no"),
            "vin_or_sor": st.get("vin_or_sor"),
            "comment": st.get("comment"),
            "applicant": st.get("applicant"),
        }
        ok = post_to_sheet(payload)
        if ok:
            send_message(chat_id, "✅ Заявка отправлена в лист «Новые заявки». Спасибо!")
        else:
            send_message(chat_id, "❌ Не удалось записать в таблицу. Проверь доступ к Web App.")
        # очищаем состояние
        STATE.pop(chat_id, None)
        answer_callback_query(cb_id)
        return {"ok": True}

    if data == "cancel":
        edit_message_reply_markup(chat_id, message_id, {"inline_keyboard": []})
        send_message(chat_id, "Отменено.")
        STATE.pop(chat_id, None)
        answer_callback_query(cb_id)
        return {"ok": True}

    answer_callback_query(cb_id)
    return {"ok": True}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("orders_service:app", host="0.0.0.0", port=8000)
