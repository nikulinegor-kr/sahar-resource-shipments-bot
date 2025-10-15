# server.py
import os
import io
import csv
import time
import html
import requests
from typing import Dict, Any, List, Optional
from datetime import datetime, date, timedelta

from fastapi import FastAPI, Request, Header, HTTPException, Body
from fastapi.responses import JSONResponse

# ===== Конфигурация =====
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()                 # id группы для уведомлений
SHEET_CSV_URL = os.getenv("SHEET_CSV_URL", "").strip()     # CSV для команд /my /status и т.д.
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()   # общий секрет для /tg (опц.) и /notify (обязательно)

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

app = FastAPI(title="BotSnab • Поставки ТМЦ", version="1.3.2")

# ===== Кэш таблицы (для команд) =====
_CSV_CACHE: Dict[str, Any] = {"ts": 0.0, "rows": []}
CSV_TTL = 60.0  # 1 минута

# ===== Вспомогательные =====
def tg_send_message(chat_id: int | str, text: str, parse_mode="HTML"):
    if not BOT_TOKEN:
        return {"ok": False, "error": "BOT_TOKEN is empty"}
    r = requests.post(
        f"{TG_API}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode, "disable_web_page_preview": True},
        timeout=15
    )
    try:
        return r.json()
    except Exception:
        return {"ok": False, "text": r.text}

def esc(s: Optional[str]) -> str:
    return html.escape((s or "").strip())

def _parse_date(s: str) -> Optional[date]:
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
    now = time.time()
    if _CSV_CACHE["rows"] and now - _CSV_CACHE["ts"] < CSV_TTL:
        return _CSV_CACHE["rows"]
    if not SHEET_CSV_URL:
        return []
    r = requests.get(SHEET_CSV_URL, timeout=20)
    r.raise_for_status()
    # Убираем BOM и читаем CSV
    text = r.content.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    data = list(reader)
    if not data:
        return []
    headers = [h.strip() for h in data[0]]
    rows = []
    for raw in data[1:]:
        row = {}
        for i, h in enumerate(headers):
            row[h] = (raw[i].strip() if i < len(raw) else "")
        rows.append(row)
    _CSV_CACHE.update({"ts": now, "rows": rows})
    return rows

def _norm(s: str) -> str:
    """Нормализация ключей: нижний регистр, убрать лишние пробелы и двойные пробелы."""
    return " ".join((s or "").strip().lower().split())

def _field(row: Dict[str, str], *names: str) -> str:
    """
    Берём значение по одному из синонимов заголовка.
    Учитываем строчные/пробелы. Работает даже если в таблице ' Комментарий  '.
    """
    # Прямая попытка
    for n in names:
        if n in row:
            return row.get(n, "")

    # По нормализованной карте
    norm_map = {_norm(k): k for k in row.keys()}
    for n in names:
        nn = _norm(n)
        if nn in norm_map:
            return row.get(norm_map[nn], "")

    return ""

# полный пул синонимов для "Комментарий"
COMMENT_HEADERS = [
    "Комментарий", "Комментарии", "Коментарий", "комментарий", "коментарий",
    "Примечание", "Примечания", "Прим.", "Примечание к заявке", "Комментарий к заявке",
    "Комментарий к отгрузке", "Комментарий заказчика", "Заметки", "Notes"
]

def _normalize_row(row: Dict[str, str]) -> Dict[str, Any]:
    return {
        "request": _field(row, "Заявка", "Название", "Наименование"),
        "priority": _field(row, "Приоритет"),
        "status": _field(row, "Статус"),
        "ship_date": _parse_date(_field(row, "Дата/О", "Дата О", "Дата отгрузки")),
        "arrive_date": _parse_date(_field(row, "Дата/Д", "Дата Д", "Дата прибытия")),
        "tk": _field(row, "ТК", "Тк", "Транспортная компания"),
        "ttn": _field(row, "№ ТТН", "ТТН", "Номер ТТН"),
        "applicant": _field(row, "Заявитель", "Ответственный", "Исполнитель"),
        # комментарий подхватим из любого из заданных заголовков
        "comment": next(( _field(row, h) for h in COMMENT_HEADERS if _field(row, h) ), ""),
    }

def _load_data() -> List[Dict[str, Any]]:
    return [_normalize_row(r) for r in _load_csv_rows()]

# ===== Форматирование карточки =====
def _fmt_card(item: Dict[str, Any]) -> str:
    parts = [
        "📦 <b>Уведомление о заявке</b>",
        f"🧾 <b>Заявка:</b> {esc(item.get('request'))}" if item.get("request") else "",
        f"⭐ <b>Приоритет:</b> {esc(item.get('priority'))}" if item.get("priority") else "",
        f"🚚 <b>Статус:</b> {esc(item.get('status'))}" if item.get("status") else "",
        f"📅 <b>Дата отгрузки:</b> {_format_date_long(item.get('ship_date'))}" if item.get("ship_date") else "",
        f"📦 <b>Дата прибытия:</b> {_format_date_long(item.get('arrive_date'))}" if item.get("arrive_date") else "",
        f"🚛 <b>ТК:</b> {esc(item.get('tk'))}" if item.get("tk") else "",
        f"📄 <b>№ ТТН:</b> {esc(item.get('ttn'))}" if item.get("ttn") else "",
        f"👤 <b>Заявитель:</b> {esc(item.get('applicant'))}" if item.get("applicant") else "",
        f"📝 <b>Комментарий:</b> {esc(item.get('comment') or '—')}",
    ]
    return "\n".join([p for p in parts if p])

def _reply_list(chat_id: int, title: str, items: List[Dict[str, Any]]):
    if not items:
        tg_send_message(chat_id, f"❌ Ничего не найдено по запросу <b>{esc(title)}</b>")
        return
    msg = f"🔎 <b>{esc(title)}</b>\n\n" + "\n\n".join(_fmt_card(i) for i in items[:10])
    tg_send_message(chat_id, msg)

# ===== Команды =====
def handle_command(text: str, chat_id: int, from_user: dict, bot_username: str):
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    if '@' in cmd:
        base, at = cmd.split('@', 1)
        if at.lower() != bot_username.lower():
            return
        cmd = base
    data = _load_data()
    today = date.today()

    if cmd == "/start":
        tg_send_message(chat_id, "👋 Бот снабжения готов к работе. Напиши /help для списка команд.")
    elif cmd == "/help":
        tg_send_message(chat_id,
            "📦 <b>Команды BotSnab</b>\n"
            "• /start — начать работу\n"
            "• /my — мои заявки (по ФИО или username)\n"
            "• /status <b>СТАТУС</b> — заявки по статусу\n"
            "• /today — отгрузки сегодня\n"
            "• /week — отгрузки на этой неделе\n"
            "• /search <b>ТЕКСТ</b> — поиск заявки по названию\n"
            "• /priority — аварийные заявки\n"
            "• /last — последние заявки\n"
            "• /id — показать ваш Telegram ID"
        )
    elif cmd == "/id":
        tg_send_message(chat_id, f"Ваш Telegram ID: <code>{from_user.get('id')}</code>")
    elif cmd == "/my":
        name = (from_user.get("first_name") or "").lower()
        username = (from_user.get("username") or "").lower()
        items = [r for r in data if username in (r["applicant"] or "").lower() or name in (r["applicant"] or "").lower()]
        _reply_list(chat_id, "Ваши заявки", items)
    elif cmd == "/status":
        if not arg:
            tg_send_message(chat_id, "❗ Укажите статус, например: /status В пути")
        else:
            items = [r for r in data if (r["status"] or "").lower() == arg.lower()]
            _reply_list(chat_id, f"Заявки со статусом «{arg}»", items)
    elif cmd == "/today":
        items = [r for r in data if r["ship_date"] == today]
        _reply_list(chat_id, "Отгрузки сегодня", items)
    elif cmd == "/week":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        items = [r for r in data if r["ship_date"] and start <= r["ship_date"] <= end]
        _reply_list(chat_id, "Поставки на этой неделе", items)
    elif cmd == "/search":
        if not arg:
            tg_send_message(chat_id, "❗ Укажите текст для поиска: /search фильтра")
        else:
            items = [r for r in data if arg.lower() in (r["request"] or "").lower()]
            _reply_list(chat_id, f"Результаты поиска: {arg}", items)
    elif cmd == "/priority":
        items = [r for r in data if (r["priority"] or "").lower().startswith("авар")]
        _reply_list(chat_id, "Аварийные заявки", items)
    elif cmd == "/last":
        _reply_list(chat_id, "Последние заявки", data[-10:])
    else:
        tg_send_message(chat_id, "Неизвестная команда. Напиши /help.")

# ===== РОУТЫ =====
@app.get("/")
def root():
    return {"ok": True, "routes": ["/", "/health", "/tg (GET/POST)", "/notify"]}

@app.get("/health")
def health():
    return {"ok": True, "service": "snab-bot", "webhook": "/tg"}

@app.get("/tg")
def tg_get():
    return {"ok": True, "route": "/tg"}

@app.post("/tg")
async def tg_post(req: Request, x_telegram_bot_api_secret_token: Optional[str] = Header(None)):
    # Если хотите проверять секрет Telegram webhook — раскомментируйте и добавьте secret_token при setWebhook
    # if WEBHOOK_SECRET and (x_telegram_bot_api_secret_token or "") != WEBHOOK_SECRET:
    #     raise HTTPException(status_code=403, detail="Invalid webhook secret")

    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="BOT_TOKEN not configured")

    data = await req.json()
    message = data.get("message", {})
    text = (message.get("text") or "").strip()
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    user = message.get("from", {})

    if text.startswith("/"):
        me = requests.get(f"{TG_API}/getMe", timeout=10).json()
        bot_username = me["result"]["username"] if me.get("ok") else ""
        handle_command(text, chat_id, user, bot_username)

    return {"ok": True}

# ===== /notify — для Google Apps Script =====
@app.post("/notify")
def notify_from_sheet(
    payload: Dict[str, Any] = Body(...),
    authorization: str = Header(default="")
):
    """
    Принимает JSON из Google Apps Script и шлёт сообщение в группу.
    Поля (опциональны): order_id, priority, status, ship_date, arrival_date, carrier, ttn, applicant, comment, chat_id
    Авторизация: Authorization: Bearer <WEBHOOK_SECRET>
    """
    if WEBHOOK_SECRET and authorization != f"Bearer {WEBHOOK_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    chat = payload.get("chat_id") or CHAT_ID
    if not chat:
        raise HTTPException(status_code=400, detail="CHAT_ID is empty")

    request_name = payload.get("order_id") or payload.get("request") or payload.get("name")
    priority     = payload.get("priority")
    status       = payload.get("status")
    ship_date    = _parse_date(payload.get("ship_date", ""))
    arrive_date  = _parse_date(payload.get("arrival_date", "")) or _parse_date(payload.get("arrive_date", ""))
    tk           = payload.get("carrier") or payload.get("tk")
    ttn          = payload.get("ttn") or payload.get("waybill")
    applicant    = payload.get("applicant") or payload.get("responsible_name") or payload.get("responsible", {}).get("name")
    comment      = payload.get("comment")

    item = {
        "request": request_name,
        "priority": priority,
        "status": status,
        "ship_date": ship_date,
        "arrive_date": arrive_date,
        "tk": tk,
        "ttn": ttn,
        "applicant": applicant,
        "comment": comment,  # если None/"" — в карточке будет "—"
    }

    text = _fmt_card(item)
    res = tg_send_message(chat, text)
    if not res.get("ok"):
        raise HTTPException(status_code=502, detail=f"Telegram error: {res}")
    return {"ok": True}
