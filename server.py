import os
import html
import requests
from typing import Optional, Dict, Any

from fastapi import FastAPI, Request, Header, HTTPException

# =============================
# ENV
# =============================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()  # группа/чат для уведомлений
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()  # секрет для /notify (из таблицы)
SHEET_CSV_URL = os.getenv("SHEET_CSV_URL", "").strip()  # опционально, для команд
SHEET_SCRIPT_URL = os.getenv("SHEET_SCRIPT_URL", "").strip()  # URL Web App GAS (конец /exec)
SHEET_SCRIPT_SECRET = os.getenv("SHEET_SCRIPT_SECRET", "").strip()  # тот же секрет, что и в GAS

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

app = FastAPI(title="Snab Notify Bot", version="1.2.0")

# =============================
# Формат входящего JSON из таблицы
# =============================
class OrderPayloadDict(Dict[str, Any]):
    """подсказка для линтеров, обычный dict"""


# =============================
# Telegram helpers
# =============================
def tg_send_message(
    text: str,
    parse_mode: str = "HTML",
    reply_markup: Optional[dict] = None,
    chat_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Отправка сообщения в TG."""
    if not BOT_TOKEN:
        return {"ok": False, "error": "BOT_TOKEN missing"}

    url = f"{TG_API}/sendMessage"
    payload = {
        "chat_id": chat_id or CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tg_answer_callback(callback_query_id: str):
    """Убрать «часики» после нажатия кнопки."""
    if not BOT_TOKEN or not callback_query_id:
        return
    try:
        requests.post(
            f"{TG_API}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id},
            timeout=5,
        )
    except Exception:
        pass


# =============================
# Текст уведомления
# =============================
def format_order_text(data: Dict[str, Any]) -> str:
    g = lambda k: (data.get(k) or "").strip()

    lines = ["📦 Уведомление о заявке"]
    order = g("order_id") or "—"
    lines.append(f"🧾 Заявка: {html.escape(order)}")

    if g("priority"):
        lines.append(f"⭐ Приоритет: {html.escape(g('priority'))}")
    if g("status"):
        lines.append(f"🚚 Статус: {html.escape(g('status'))}")
    if g("carrier"):
        lines.append(f"🚛 ТК: {html.escape(g('carrier'))}")
    if g("ttn"):
        lines.append(f"📄 № ТТН: {html.escape(g('ttn'))}")
    if g("ship_date"):
        lines.append(f"📅 Дата отгрузки: {html.escape(g('ship_date'))}")
    if g("arrival"):
        lines.append(f"📅 Дата прибытия: {html.escape(g('arrival'))}")
    if g("applicant"):
        lines.append(f"👤 Заявитель: {html.escape(g('applicant'))}")
    if g("comment"):
        lines.append(f"📝 Комментарий: {html.escape(g('comment'))}")

    return "\n".join(lines)


def build_inline_keyboard_for_order(data: Dict[str, Any]) -> Optional[dict]:
    """Кнопку показываем только при статусе «Доставлено в ТК»."""
    status = (data.get("status") or "").strip().lower()
    order_id = (data.get("order_id") or "").strip()
    if not order_id:
        return None

    if status == "доставлено в тк":
        cb = f"received|{order_id}"
        return {"inline_keyboard": [[{"text": "✅ ТМЦ ПОЛУЧЕНО", "callback_data": cb}]]}
    return None


# =============================
# Служебные роуты
# =============================
@app.get("/")
def root():
    return {"ok": True, "routes": ["/", "/health", "/tg (GET/POST)", "/notify", "/docs"]}


@app.get("/health")
def health():
    return {"ok": True, "service": "snab-bot", "webhook": "/tg"}


@app.get("/tg")
def get_tg():
    return {"ok": True, "route": "/tg"}


# =============================
# Получение из Google Sheets (таблицы)
# =============================
@app.post("/notify")
async def notify(req: Request, authorization: Optional[str] = Header(None)):
    # простая защита секретом
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization.split("Bearer ")[-1].strip()
    if token != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        data: OrderPayloadDict = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Body must be JSON object")

    text = format_order_text(data)
    keyboard = build_inline_keyboard_for_order(data)
    res = tg_send_message(text, reply_markup=keyboard)
    return {"ok": True, "telegram_response": res}


# =============================
# Telegram webhook: команды и callback-кнопки
# =============================
@app.post("/tg")
async def telegram_webhook(req: Request):
    update = await req.json()

    # 1) callback-кнопки
    if "callback_query" in update:
        cq = update["callback_query"]
        data = cq.get("data", "")
        cb_id = cq.get("id")
        tg_answer_callback(cb_id)

        if data.startswith("received|"):
            order_id = data.split("|", 1)[1].strip()
            # шлём в Apps Script команду на обновление
            if SHEET_SCRIPT_URL and SHEET_SCRIPT_SECRET:
                payload = {
                    "action": "received",
                    "order_id": order_id,
                    "secret": SHEET_SCRIPT_SECRET,  # т.к. GAS не отдаёт заголовки в doPost
                }
                try:
                    resp = requests.post(
                        SHEET_SCRIPT_URL, json=payload, timeout=10
                    )
                    ok = 200 <= resp.status_code < 300
                    if ok:
                        tg_send_message(f"✅ Получение подтверждено по заявке <b>{html.escape(order_id)}</b>.")
                    else:
                        tg_send_message(
                            f"⚠️ Не удалось обновить статус для <b>{html.escape(order_id)}</b>: {resp.text}"
                        )
                except Exception as e:
                    tg_send_message(f"⚠️ Ошибка связи с таблицей по заявке <b>{html.escape(order_id)}</b>: {e}")

        return {"ok": True, "handled": "callback"}

    # 2) простые команды (минимум)
    if "message" in update:
        msg = update["message"]
        text = (msg.get("text") or "").strip()
        chat_id = msg.get("chat", {}).get("id")

        if text.startswith("/start"):
            tg_send_message(
                "Привет! Я бот снабжения.\nКоманды: /help — список команд /id — показать ваш Telegram ID",
                chat_id=chat_id,
            )
        elif text.startswith("/help"):
            tg_send_message(
                "Доступные команды:\n/start — начать\n/help — список команд\n/id — показать ваш Telegram ID",
                chat_id=chat_id,
            )
        elif text.startswith("/id"):
            user = msg.get("from", {})
            uid = user.get("id")
            uname = user.get("username")
            tg_send_message(
                f"Ваш ID: <b>{uid}</b>\nПользователь: @{uname}" if uname else f"Ваш ID: <b>{uid}</b>",
                chat_id=chat_id,
            )
        else:
            # игнор прочего текста
            pass

        return {"ok": True, "handled": "message"}

    return {"ok": True}
