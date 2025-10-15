# server.py
import os
import io
import csv
import time
import json
import html
import math
import requests
from typing import Dict, Any, List, Optional
from datetime import datetime, date, timedelta

from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import JSONResponse

# ====== Конфигурация из окружения ======
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()  # опционально
SHEET_CSV_URL = os.getenv("SHEET_CSV_URL", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()  # зарезервировано

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

# ====== Приложение ======
app = FastAPI(title="BotSnab • TMC Shipments", version="1.1.0")

# ====== Кэш CSV (чтобы не долбить таблицу каждую команду) ======
_CSV_CACHE: Dict[str, Any] = {
    "ts": 0.0,
    "rows": [],
    "headers": []
}
CSV_TTL = 60.0  # сек

# ====== Вспомогательные ======
def tg_send_message(chat_id: int | str, text: str, parse_mode: str = "HTML") -> Dict[str, Any]:
    if not BOT_TOKEN:
        return {"ok": False, "error": "BOT_TOKEN is empty"}
    try:
        r = requests.post(
            f"{TG_API}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True
            },
            timeout=15
        )
        try:
            return r.json()
        except Exception:
            return {"ok": False, "status": r.status_code, "text": r.text}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def esc(s: Optional[str]) -> str:
    return html.escape((s or "").strip())


def _parse_date(s: str) -> Optional[date]:
    """
    Поддержка форматов:
      - 2025-10-13
      - 13.10.25, 13.10.2025
      - 13/10/2025
    """
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%y", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    return None


def _format_date_long(d: Optional[date]) -> str:
    if not d:
        return ""
    months = [
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря"
    ]
    return f"{d.day} {months[d.month - 1]} {d.year}"


def _load_csv_rows() -> List[Dict[str, str]]:
    """Загрузить строки из CSV с кэшем."""
    now = time.time()
    if _CSV_CACHE["rows"] and now - _CSV_CACHE["ts"] < CSV_TTL:
        return _CSV_CACHE["rows"]

    if not SHEET_CSV_URL:
        _CSV_CACHE.update({"ts": now, "rows": [], "headers": []})
        return []

    r = requests.get(SHEET_CSV_URL, timeout=20)
    r.raise_for_status()
    content = r.content.decode("utf-8")
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    if not rows:
        _CSV_CACHE.update({"ts": now, "rows": [], "headers": []})
        return []

    headers = [h.strip() for h in rows[0]]
    data = []
    for raw in rows[1:]:
        row = {}
        for i, h in enumerate(headers):
            row[h] = (raw[i].strip() if i < len(raw) else "")
        data.append(row)

    _CSV_CACHE.update({"ts": now, "rows": data, "headers": headers})
    return data


def _field(row: Dict[str, str], *candidates: str) -> str:
    """Вытянуть значение по одному из возможных заголовков (на всякий случай)."""
    for key in candidates:
        if key in row:
            return row.get(key, "")
    # иногда заголовки с лишними пробелами
    keys = {k.strip(): k for k in row.keys()}
    for key in candidates:
        if key in keys:
            return row.get(keys[key], "")
    return ""


def _who_is_applicant(row: Dict[str, str]) -> str:
    # Основное поле — «Заявитель»
    v = _field(row, "Заявитель", "Заявитель:", "Заявитель(ФИО)")
    if not v:
        # запасной вариант — «Исполнитель»
        v = _field(row, "Исполнитель", "Ответственный")
    return v


def _normalize_row(row: Dict[str, str]) -> Dict[str, Any]:
    """Приведение полей к единым именам для логики команд."""
    return {
        "request": _field(row, "Заявка", "Название", "Наименование"),
        "priority": _field(row, "Приоритет"),
        "status": _field(row, "Статус"),
        "ship_date": _parse_date(_field(row, "Дата/О", "Дата О", "Дата отгрузки")),
        "arrive_date": _parse_date(_field(row, "Дата/Д", "Дата Д", "Дата прибытия")),
        "tk": _field(row, "ТК", "Тк", "Транспортная компания"),
        "ttn": _field(row, "№ ТТН", "№ТТН", "ТТН"),
        "applicant": _who_is_applicant(row),
        "raw": row,  # оставим всё исходное
    }


def _load_data() -> List[Dict[str, Any]]:
    return [_normalize_row(r) for r in _load_csv_rows()]


def _fmt_card(item: Dict[str, Any]) -> str:
    """Красивая карточка заявки."""
    parts = [
        "📦 <b>Уведомление о заявке</b>",
        f"🧾 <b>Заявка:</b> {esc(item['request'])}" if item["request"] else "",
        f"⭐ <b>Приоритет:</b> {esc(item['priority'])}" if item["priority"] else "",
        f"🚚 <b>Статус:</b> {esc(item['status'])}" if item["status"] else "",
        f"📅 <b>Дата отгрузки:</b> {_format_date_long(item['ship_date'])}" if item["ship_date"] else "",
        f"📦 <b>Дата прибытия:</b> {_format_date_long(item['arrive_date'])}" if item["arrive_date"] else "",
        f"🚛 <b>ТК:</b> {esc(item['tk'])}" if item["tk"] else "",
        f"📄 <b>№ ТТН:</b> {esc(item['ttn'])}" if item["ttn"] else "",
        f"👤 <b>Заявитель:</b> {esc(item['applicant'])}" if item["applicant"] else "",
    ]
    return "\n".join([p for p in parts if p])


def _paginate(items: List[Dict[str, Any]], limit: int = 10) -> List[List[Dict[str, Any]]]:
    if limit <= 0:
        limit = 10
    pages = []
    for i in range(0, len(items), limit):
        pages.append(items[i:i+limit])
    return pages


def _reply_list(chat_id: int, title: str, items: List[Dict[str, Any]], limit: int = 6):
    if not items:
        tg_send_message(chat_id, f"Ничего не найдено по запросу: {esc(title)}")
        return
    pages = _paginate(items, limit)
    for idx, page in enumerate(pages, 1):
        header = f"🔎 <b>{esc(title)}</b> • стр. {idx}/{len(pages)}"
        body = "\n\n".join(_fmt_card(x) for x in page)
        tg_send_message(chat_id, f"{header}\n\n{body}")


# ====== HELP ======
def get_help_text() -> str:
    return (
        "📦 <b>BotSnab — команды</b>\n\n"
        "• /start — начать работу с ботом\n"
        "• /help — список доступных команд\n"
        "• /id — показать ваш Telegram ID\n\n"
        "👤 <b>Личные запросы</b>\n"
        "• /my — показать ваши заявки (по «Заявитель» или «Исполнитель»)\n"
        "• /status &lt;статус&gt; — заявки по статусу (напр.: /status В пути)\n"
        "• /today — отгрузки сегодня\n"
        "• /week — отгрузки на этой неделе\n"
        "• /search &lt;текст&gt; — найти заявку по названию\n"
        "• /priority — все аварийные заявки\n"
        "• /last — последние обновления по заявкам\n\n"
        "ℹ️ В группе пишите команду отдельным сообщением. Если включена privacy, используйте @имябота: /help@ИмяБота"
    )


# ====== Обработка команд ======
def handle_command(text: str, chat_id: int, from_user: dict, bot_username: str):
    # отделим /cmd от аргументов
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    # если команда прислана как /help@BotName — оставим только /help, но проверим имя
    if '@' in cmd:
        base, at = cmd.split('@', 1)
        if at.lower() != bot_username.lower():
            return  # это не наш бот
        cmd = base

    user_id = from_user.get("id")
    user_name = from_user.get("first_name", "")

    data = None  # будем лениво загружать CSV по требованию

    def ensure_data():
        nonlocal data
        if data is None:
            data = _load_data()
        return data

    if cmd == "/start":
        tg_send_message(chat_id, "👋 Готов к работе. Напишите /help, чтобы увидеть все команды.")
        return

    if cmd == "/help":
        tg_send_message(chat_id, get_help_text())
        return

    if cmd == "/id":
        tg_send_message(chat_id, f"Ваш Telegram ID: <code>{user_id}</code>")
        return

    if cmd == "/my":
        rows = ensure_data()
        # «Моими» считаем строки, где поле «Заявитель» или «Исполнитель» содержит имя/username
        # Логика примитивная, при необходимости уточним.
        query_tokens = []
        if from_user.get("username"):
            query_tokens.append(from_user["username"])
        if user_name:
            query_tokens.append(user_name)

        def _belongs(r):
            who = (r["applicant"] or "").lower()
            for t in query_tokens:
                if t and t.lower() in who:
                    return True
            return False

        items = [r for r in rows if _belongs(r)]
        _reply_list(chat_id, "Ваши заявки", items)
        return

    if cmd == "/status":
        status = args.strip()
        if not status:
            tg_send_message(chat_id, "Укажите статус, например: <code>/status В пути</code>")
            return
        rows = ensure_data()
        items = [r for r in rows if (r["status"] or "").lower() == status.lower()]
        _reply_list(chat_id, f"Заявки со статусом «{status}»", items)
        return

    if cmd == "/today":
        rows = ensure_data()
        today = date.today()
        items = [r for r in rows if r["ship_date"] == today]
        _reply_list(chat_id, "Отгрузки сегодня", items)
        return

    if cmd == "/week":
        rows = ensure_data()
        today = date.today()
        start_week = today - timedelta(days=today.weekday())
        end_week = start_week + timedelta(days=6)
        items = [r for r in rows if r["ship_date"] and start_week <= r["ship_date"] <= end_week]
        _reply_list(chat_id, "Поставки на этой неделе", items)
        return

    if cmd == "/search":
        q = args.strip()
        if not q:
            tg_send_message(chat_id, "Укажите текст для поиска: <code>/search фильтра</code>")
            return
        rows = ensure_data()
        qq = q.lower()
        items = [r for r in rows if qq in (r["request"] or "").lower()]
        _reply_list(chat_id, f"Поиск: «{q}»", items)
        return

    if cmd == "/priority":
        rows = ensure_data()
        items = [r for r in rows if (r["priority"] or "").lower().startswith("авар")]
        _reply_list(chat_id, "Аварийные заявки", items)
        return

    if cmd == "/last":
        # Без полноценного «updated_at» в CSV покажем последние N строк как «новые/обновлённые».
        rows = ensure_data()
        items = rows[-10:] if len(rows) > 10 else rows
        _reply_list(chat_id, "Последние обновления", items)
        return

    # Неизвестная команда — кинем подсказку
    tg_send_message(chat_id, "Не понимаю команду. Напишите /help")


# ====== Роуты сервера ======

@app.get("/")
def root():
    return {"ok": True, "routes": ["/", "/health", "/tg (GET/POST)", "/notify (reserved)", "/docs"]}


@app.get("/health")
def health():
    return {"ok": True, "service": "snab-bot", "webhook": "/tg"}


@app.get("/tg")
def tg_probe():
    return {"ok": True, "route": "/tg"}


@app.post("/tg")
async def tg_webhook(req: Request, x_telegram_bot_api_secret_token: Optional[str] = Header(None)):
    """
    Вебхук Telegram. Если в BotFather задан Secret Token — можно сверять с WEBHOOK_SECRET.
    """
    if WEBHOOK_SECRET:
        # Если вы включили секрет в BotFather — раскомментируйте проверку:
        # if (x_telegram_bot_api_secret_token or "") != WEBHOOK_SECRET:
        #     raise HTTPException(status_code=403, detail="Invalid webhook secret")
        pass

    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="BOT_TOKEN not configured")

    try:
        update = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Bad JSON")

    # Определим имя бота (для /help@ИмяБота и т.п.)
    bot_username = ""
    try:
        me = requests.get(f"{TG_API}/getMe", timeout=10).json()
        if me.get("ok"):
            bot_username = me["result"]["username"]
    except Exception:
        pass

    message = update.get("message") or update.get("edited_message") or {}
    text = (message.get("text") or "").strip()
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    from_user = message.get("from") or {}

    # Реагируем только на команды (начинаются с /)
    if text.startswith("/"):
        handle_command(text, chat_id, from_user, bot_username)
    else:
        # Игнор прочих сообщений, чтобы не мусорить чат
        pass

    return JSONResponse({"ok": True})


# ====== (опционально) тестовая отправка из кода ======
@app.post("/notify")
def notify_example():
    if not CHAT_ID:
        return {"ok": False, "error": "CHAT_ID is empty"}
    # Пример карточки «как у уведомлений из таблицы»
    msg = (
        "📦 <b>Уведомление о заявке</b>\n"
        "🧾 <b>Заявка:</b> Пример\n"
        "⭐ <b>Приоритет:</b> Аварийно\n"
        "🚚 <b>Статус:</b> В пути\n"
        "📅 <b>Дата отгрузки:</b> 13 октября 2025\n"
        "👤 <b>Заявитель:</b> Иванов И.И."
    )
    res = tg_send_message(CHAT_ID, msg)
    return res
