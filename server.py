import os
import html
import requests
from fastapi import FastAPI, Request, Header, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any, List

# ========= ENV =========
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()             # группа для /notify (из таблиц)
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
SHEET_CSV_URL = os.getenv("SHEET_CSV_URL", "").strip() # опционально, для будущих команд из таблиц

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

app = FastAPI(title="Snab Notify Bot", version="1.3.0")

# ========= MODELS =========
class OrderPayload(BaseModel):
    order_id: Optional[str] = None
    recipient: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    ship_date: Optional[str] = None
    arrival: Optional[str] = None
    carrier: Optional[str] = None
    ttn: Optional[str] = None
    applicant: Optional[str] = None
    comment: Optional[str] = None


# ========= HELPERS =========
def tg_send_message_to(chat_id: str | int, text: str, parse_mode: str = "HTML") -> Dict[str, Any]:
    """Отправить сообщение в конкретный чат (для ответов на команды)."""
    if not BOT_TOKEN:
        return {"ok": False, "error": "BOT_TOKEN missing"}
    url = f"{TG_API}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    r = requests.post(url, json=payload, timeout=15)
    try:
        return r.json()
    except Exception:
        return {"ok": r.ok, "status": r.status_code, "text": r.text}


def tg_send_to_group(text: str) -> Dict[str, Any]:
    """Отправить в группу по заданному CHAT_ID (для /notify из таблиц)."""
    if not BOT_TOKEN or not CHAT_ID:
        return {"ok": False, "error": "BOT_TOKEN or CHAT_ID missing"}
    return tg_send_message_to(CHAT_ID, text)


def format_order_text(data: Dict[str, Any]) -> str:
    """Красивое сообщение для уведомлений из таблиц."""
    get = lambda k: (data.get(k) or "").strip()
    lines = ["📦 Уведомление о заявке"]
    order = get("order_id") or "—"
    lines.append(f"🧾 Заявка: {html.escape(order)}")

    if get("priority"):
        lines.append(f"⭐ Приоритет: {html.escape(get('priority'))}")
    if get("status"):
        lines.append(f"🚚 Статус: {html.escape(get('status'))}")
    if get("carrier"):
        lines.append(f"🚛 ТК: {html.escape(get('carrier'))}")
    if get("ttn"):
        lines.append(f"📄 № ТТН: {html.escape(get('ttn'))}")
    if get("ship_date"):
        lines.append(f"📅 Дата отгрузки: {html.escape(get('ship_date'))}")
    if get("arrival"):
        lines.append(f"📅 Дата прибытия: {html.escape(get('arrival'))}")
    if get("applicant"):
        lines.append(f"👤 Заявитель: {html.escape(get('applicant'))}")
    # комментарий отправляем только если есть осмысленный текст
    comment = get("comment")
    if comment and not all(c in "-–— " for c in comment):
        lines.append(f"📝 Комментарий: {html.escape(comment)}")
    return "\n".join(lines)


def parse_update_for_message(update: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Унифицировано достаём сообщение из update:
    - message (личка/группа)
    - channel_post (если бот в канале)
    Возвращаем dict с полями chat_id, text, from_user.
    """
    msg = update.get("message") or update.get("channel_post")
    if not msg:
        return None
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = msg.get("text") or ""
    from_user = msg.get("from") or msg.get("author_signature") or {}
    return {"chat_id": chat_id, "text": text, "from_user": from_user}


def normalize_command(text: str, bot_username: Optional[str]) -> str:
    """
    /start, /start@BotName -> /start
    /help@BotName -> /help и т.д.
    """
    if not text.startswith("/"):
        return ""
    cmd = text.split()[0]  # первое слово
    if "@" in cmd and bot_username:
        # удалим суффикс @botname
        name = bot_username.lower()
        if cmd.lower().endswith(f"@{name}"):
            cmd = cmd[: cmd.index("@")]
    return cmd.lower()


# ========= SERVICE ROUTES =========
@app.get("/")
def root():
    return {"ok": True, "routes": ["/", "/health", "/tg (GET/POST)", "/notify", "/docs"]}


@app.get("/health")
def health():
    return {"ok": True, "service": "snab-bot", "webhook": "/tg"}


@app.get("/tg")
def get_tg():
    return {"ok": True, "route": "/tg"}


# ========= NOTIFY FROM GOOGLE SHEETS =========
@app.post("/notify")
async def notify(req: Request, authorization: Optional[str] = Header(None)):
    # секрет обязателен
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization.split("Bearer ", 1)[-1].strip()
    if token != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        data = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Body must be JSON object")

    msg_text = format_order_text(data)
    res = tg_send_to_group(msg_text)
    if not res.get("ok"):
        raise HTTPException(status_code=502, detail=f"Telegram error: {res}")
    return {"ok": True, "telegram": res}


# ========= TELEGRAM WEBHOOK (COMMANDS) =========
@app.post("/tg")
async def telegram_webhook(req: Request):
    update = await req.json()
    # print(update)  # можно посмотреть в логах

    # иногда приходят service updates: my_chat_member, chat_member и т.п.
    msg_info = parse_update_for_message(update)
    if not msg_info:
        return {"ok": True, "skipped": True}

    chat_id = msg_info["chat_id"]
    text = msg_info["text"] or ""
    from_user = msg_info["from_user"] or {}
    bot_username = os.getenv("BOT_USERNAME", "").strip()  # опционально, но можно задать

    cmd = normalize_command(text, bot_username)
    if not cmd:
        # игнор всего, что не команда (или напишите авто-ответ, если надо)
        return {"ok": True}

    # --- простые команды, работают сразу ---
    if cmd == "/start":
        reply = (
            "Привет! Я бот снабжения.\n"
            "Команды:\n"
            "/help — список команд\n"
            "/id — показать ваш Telegram ID"
        )
        tg_send_message_to(chat_id, reply)
        return {"ok": True}

    if cmd == "/help":
        reply = (
            "Доступные команды:\n"
            "/start — начать\n"
            "/help — список команд\n"
            "/id — показать ваш Telegram ID\n"
            # сюда позже добавим «умные» команды, когда подключим CSV
        )
        tg_send_message_to(chat_id, reply)
        return {"ok": True}

    if cmd == "/id":
        uid = from_user.get("id", "—")
        uname = from_user.get("username")
        name = (from_user.get("first_name") or "") + " " + (from_user.get("last_name") or "")
        name = name.strip() or "—"
        who = f"@{uname}" if uname else name
        tg_send_message_to(chat_id, f"Ваш ID: <b>{uid}</b>\nПользователь: {html.escape(who)}")
        return {"ok": True}

    # --- заготовки под команды, которым нужны данные из таблицы ---
    # если будет опубликованный CSV (SHEET_CSV_URL), их можно реализовать.
    data_cmds = {"/my", "/status", "/today", "/week", "/search", "/priority", "/last"}
    if cmd in data_cmds:
        if not SHEET_CSV_URL:
            tg_send_message_to(
                chat_id,
                "Команда скоро будет доступна. Админ: добавьте переменную окружения SHEET_CSV_URL (опубликованный CSV)."
            )
            return {"ok": True}

        # здесь позже можно подключить загрузку CSV и логику
        tg_send_message_to(chat_id, "Команда в разработке (источник подключен).")
        return {"ok": True}

    # неизвестная команда
    tg_send_message_to(chat_id, "Неизвестная команда. Наберите /help")
    return {"ok": True}


# ========= LOCAL RUN =========
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000)
