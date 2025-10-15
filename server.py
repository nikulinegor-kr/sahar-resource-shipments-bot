# server.py
import os, html, requests
from typing import Optional, Dict, Any
from fastapi import FastAPI, Request, Header, HTTPException

# ====== ENV ======
BOT_TOKEN        = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID          = os.getenv("CHAT_ID", "").strip()                 # группа
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "").strip()
SHEET_SCRIPT_URL = os.getenv("SHEET_SCRIPT_URL", "").strip()        # Web App URL из Apps Script (Anyone)

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

app = FastAPI(title="Snab Notify Bot", version="1.2.0")

# ====== TG helpers ======
def tg_call(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not BOT_TOKEN:
        return {"ok": False, "error": "BOT_TOKEN missing"}
    url = f"{TG_API}/{method}"
    try:
        r = requests.post(url, json=payload, timeout=15)
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}

def tg_send_message(text: str, reply_markup: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not CHAT_ID:
        return {"ok": False, "error": "CHAT_ID missing"}
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg_call("sendMessage", payload)

def tg_answer_cbq(cbq_id: str, text: str) -> Dict[str, Any]:
    return tg_call("answerCallbackQuery", {"callback_query_id": cbq_id, "text": text, "show_alert": False})

def tg_edit_reply_markup(chat_id: int, message_id: int) -> Dict[str, Any]:
    # снимаем клавиатуру (reply_markup=None)
    return tg_call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id})

# ====== Message render ======
def format_order_text(data: Dict[str, Any]) -> str:
    g = lambda k: (data.get(k) or "").strip()
    lines = ["📦 <b>Уведомление о заявке</b>"]

    order = g("order_id") or "—"
    lines.append(f"🧾 <b>Заявка:</b> {html.escape(order)}")

    if g("priority"):
        lines.append(f"⭐ <b>Приоритет:</b> {html.escape(g('priority'))}")
    if g("status"):
        lines.append(f"🚚 <b>Статус:</b> {html.escape(g('status'))}")
    if g("carrier"):
        lines.append(f"🚛 <b>ТК:</b> {html.escape(g('carrier'))}")
    if g("ttn"):
        lines.append(f"📄 <b>№ ТТН:</b> {html.escape(g('ttn'))}")
    if g("ship_date"):
        lines.append(f"📅 <b>Дата отгрузки:</b> {html.escape(g('ship_date'))}")
    if g("arrival"):
        lines.append(f"📅 <b>Дата прибытия:</b> {html.escape(g('arrival'))}")
    if g("applicant"):
        lines.append(f"👤 <b>Заявитель:</b> {html.escape(g('applicant'))}")
    if g("comment"):
        lines.append(f"📝 <b>Комментарий:</b> {html.escape(g('comment'))}")

    return "\n".join(lines)

def build_inline_keyboard(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Кнопка показывается ТОЛЬКО когда статус = 'Доставлено в ТК' и есть order_id.
    По нажатию отправляем callback_data вида: rcvd:<order_id>
    """
    status = (data.get("status") or "").strip().lower()
    order_id = (data.get("order_id") or "").strip()
    if not order_id:
        return None
    if status != "доставлено в тк":
        return None
    return {
        "inline_keyboard": [[
            {"text": "✅ ТМЦ ПОЛУЧЕНО", "callback_data": f"rcvd:{order_id}"}
        ]]
    }

# ====== Service routes ======
@app.get("/")
def root():
    return {"ok": True, "routes": ["/", "/health", "/tg (GET/POST)", "/notify", "/docs"]}

@app.get("/health")
def health():
    return {"ok": True, "service": "snab-bot", "webhook": "/tg"}

@app.get("/tg")
def get_tg():
    return {"ok": True, "route": "/tg"}

# ====== From Google Apps Script ======
@app.post("/notify")
async def notify(req: Request, authorization: Optional[str] = Header(None)):
    # auth
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization.split("Bearer ", 1)[-1].strip()
    if token != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid token")

    # body
    try:
        data = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Body must be JSON object")

    text = format_order_text(data)
    kb = build_inline_keyboard(data)  # может быть None
    res = tg_send_message(text, reply_markup=kb)
    return {"ok": True, "telegram_response": res}

# ====== Telegram webhook: messages + callback_query ======
@app.post("/tg")
async def tg_post(req: Request):
    upd = await req.json()
    # 1) callback_query (клик по кнопке)
    if "callback_query" in upd:
        cbq = upd["callback_query"]
        cbq_id = cbq.get("id")
        from_user = cbq.get("from", {})
        msg = cbq.get("message", {})
        data = cbq.get("data") or ""
        chat_id = msg.get("chat", {}).get("id")
        message_id = msg.get("message_id")

        # ожидаем формат rcvd:<order_id>
        if data.startswith("rcvd:"):
            order_id = data.split("rcvd:", 1)[-1].strip()
            if order_id and SHEET_SCRIPT_URL:
                # дергаем Apps Script: меняем статус на "Доставлено"
                try:
                    r = requests.post(
                        SHEET_SCRIPT_URL,
                        json={"order_id": order_id, "status": "Доставлено"},
                        timeout=15,
                    )
                    ok = (r.status_code == 200) and (r.json().get("ok") is True)
                except Exception as e:
                    ok = False

                if ok:
                    tg_answer_cbq(cbq_id, "Отмечено как получено ✅")
                    # скрываем клавиатуру у сообщения
                    if chat_id and message_id:
                        tg_edit_reply_markup(chat_id, message_id)
                else:
                    tg_answer_cbq(cbq_id, "Не удалось обновить статус. Попробуйте позже.")
            else:
                tg_answer_cbq(cbq_id, "Настройка не завершена (нет URL скрипта).")
        else:
            tg_answer_cbq(cbq_id, "Неизвестное действие.")
        return {"ok": True}

    # 2) обычные сообщения — здесь можно оставить /start,/help и т.д. (опционально)
    if "message" in upd:
        text = (upd["message"].get("text") or "").strip()
        chat_id = upd["message"].get("chat", {}).get("id")
        if text == "/start":
            tg_send_message("Привет! Я бот снабжения. Буду уведомлять об изменениях заявок.")
        elif text == "/help":
            tg_send_message("Доступные команды: /start, /help")
        return {"ok": True}

    return {"ok": True, "ignored": True}

# ====== Local run ======
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000)
