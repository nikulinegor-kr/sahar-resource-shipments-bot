import os
import re
import csv
import html
import io
from datetime import datetime, timedelta, date
from typing import Optional, Dict, Any, List

import requests
from fastapi import FastAPI, Request, Header, HTTPException

# ======== НАСТРОЙКИ И ОКРУЖЕНИЕ ========
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()               # можно не использовать, если отвечаем по chat_id из апдейта
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
SHEET_CSV_URL = os.getenv("SHEET_CSV_URL", "").strip()

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

app = FastAPI(title="Snab Notify Bot", version="1.3.0")


# ======== УТИЛИТЫ ========
def tg_send_message(chat_id: str, text: str, parse_mode: str = "HTML") -> Dict[str, Any]:
    """Отправка сообщения в Telegram."""
    if not BOT_TOKEN:
        return {"ok": False, "error": "BOT_TOKEN missing"}

    url = f"{TG_API}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=15)
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).replace("\u00A0", " ").strip().lower()


def _to_date(v: str) -> Optional[date]:
    """Пытаемся распарсить даты вида 13.10.25 / 13.10.2025 / 2025-10-13 и т.п."""
    if not v:
        return None
    s = str(v).strip()
    for fmt in ("%d.%m.%y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    # иногда Google CSV даёт ISO с временем
    try:
        return datetime.fromisoformat(s).date()
    except Exception:
        return None


# колонки — гибкий поиск по русским заголовкам
HEADER_MAP = {
    "order":      ["заявка", "название", "наименование"],
    "priority":   ["приоритет"],
    "status":     ["статус"],
    "ship_date":  ["дата/о", "дата отгрузки", "дата о"],
    "arrival":    ["дата/д", "дата прибытия", "дата д"],
    "carrier":    ["тк", "т.к.", "перевозчик"],
    "ttn":        ["№ ттн", "ттн", "накладная"],
    "applicant":  ["заявитель", "ответственный", "инициатор"],
    "comment":    ["комментарий", "коментарий", "комментарии", "примечание", "коммент"],
}


def _header_index(row: List[str]) -> Dict[str, int]:
    idx = {}
    normed = [_norm(h) for h in row]
    for key, variants in HEADER_MAP.items():
        for v in variants:
            if v in normed:
                idx[key] = normed.index(v)
                break
    return idx


def load_rows() -> List[Dict[str, Any]]:
    """Загружаем CSV из опубликованной таблицы и нормализуем поля."""
    if not SHEET_CSV_URL:
        raise RuntimeError("SHEET_CSV_URL is not configured")
    r = requests.get(SHEET_CSV_URL, timeout=20)
    r.raise_for_status()
    text = r.content.decode("utf-8")
    # используем csv.reader, чтобы гибко работать с нестандартными заголовками
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return []

    header = rows[0]
    idx = _header_index(header)

    def pick(cols, name):
        i = idx.get(name)
        return cols[i].strip() if (i is not None and i < len(cols)) else ""

    data: List[Dict[str, Any]] = []
    for line in rows[1:]:
        if not any(line):
            continue
        rec = {
            "order":     pick(line, "order"),
            "priority":  pick(line, "priority"),
            "status":    pick(line, "status"),
            "ship_date": pick(line, "ship_date"),
            "arrival":   pick(line, "arrival"),
            "carrier":   pick(line, "carrier"),
            "ttn":       pick(line, "ttn"),
            "applicant": pick(line, "applicant"),
            "comment":   pick(line, "comment"),
        }
        data.append(rec)
    return data


def dt_ru(d: date) -> str:
    months = [
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    ]
    return f"{d.day} {months[d.month-1]} {d.year}"


def short_line(rec: Dict[str, Any]) -> str:
    parts = [f"• <b>{html.escape(rec.get('order') or '—')}</b>"]
    if rec.get("status"):
        parts.append(f"— {html.escape(rec['status'])}")
    if rec.get("carrier"):
        parts.append(f"— ТК: {html.escape(rec['carrier'])}")
    if rec.get("ttn"):
        parts.append(f"— ТТН: {html.escape(rec['ttn'])}")
    return " ".join(parts)


# ======== ФОРМАТИРОВАННЫЕ УВЕДОМЛЕНИЯ ИЗ APPS SCRIPT ========
def format_order_text(data: Dict[str, Any]) -> str:
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
    if get("comment"):
        lines.append(f"📝 Комментарий: {html.escape(get('comment'))}")
    return "\n".join(lines)


# ======== СЛУЖЕБНЫЕ РОУТЫ ========
@app.get("/")
def root():
    return {"ok": True, "routes": ["/", "/health", "/tg (GET/POST)", "/notify", "/docs"]}


@app.get("/health")
def health():
    return {"ok": True, "service": "snab-bot", "webhook": "/tg", "csv": bool(SHEET_CSV_URL)}


@app.get("/tg")
def get_tg():
    return {"ok": True, "route": "/tg"}


# ======== ПРИЁМ ИЗ APPS SCRIPT (/notify) ========
@app.post("/notify")
async def notify(req: Request, authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization.split("Bearer ")[-1].strip()
    if token != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        data = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Body must be JSON object")

    # в уведомлениях шлём в общий канал/группу, если задан CHAT_ID; иначе игнор
    chat = CHAT_ID or None
    if not chat:
        return {"ok": True, "skipped": "CHAT_ID is not configured"}

    msg_text = format_order_text(data)
    res = tg_send_message(chat, msg_text)
    return {"ok": True, "telegram_response": res}


# ======== КОМАНДЫ БОТА ========
HELP_TEXT = (
    "Привет! Я бот снабжения.\n"
    "Команды:\n"
    "/start — начать\n"
    "/help — список команд\n"
    "/id — показать ваш Telegram ID\n"
    "/today — заявки с отгрузкой или прибытия сегодня\n"
    "/week — заявки на ближайшие 7 дней\n"
    "/my [имя] — ваши заявки (если имя не указано, ищу по профилю)\n"
    "/priority — аварийные заявки\n"
)


def parse_command(text: str, bot_username: Optional[str]) -> (str, str):
    """Возвращает (cmd, arg). Убираем @BotName в группах."""
    t = (text or "").strip()
    if not t.startswith("/"):
        return "", ""
    # отрезаем @BotName
    if bot_username:
        t = re.sub(fr"@{re.escape(bot_username)}\b", "", t, flags=re.IGNORECASE)
    parts = t.split(maxsplit=1)
    cmd = parts[0].lower()  # /today, /my и т.п.
    arg = parts[1].strip() if len(parts) > 1 else ""
    return cmd, arg


def pick_applicant_from_user(user: Dict[str, Any]) -> str:
    """Пытаемся сформировать строку заявителя на основе данных Telegram."""
    # Пробуем full name → username
    full = " ".join([x for x in [user.get("first_name"), user.get("last_name")] if x]).strip()
    if full:
        return full
    if user.get("username"):
        return str(user["username"])
    return ""


def cmd_today(all_rows: List[Dict[str, Any]]) -> str:
    today = date.today()
    res = []
    ship_cnt = 0
    arr_cnt = 0
    for r in all_rows:
        sd = _to_date(r.get("ship_date", ""))
        ad = _to_date(r.get("arrival", ""))
        ok = False
        if sd == today:
            ship_cnt += 1
            ok = True
        if ad == today:
            arr_cnt += 1
            ok = True
        if ok:
            res.append(short_line(r))

    if not res:
        return f"На {dt_ru(today)} заявок нет."
    head = f"Сегодня ({dt_ru(today)}): {len(res)} заявок — отгрузок: {ship_cnt}, прибытия: {arr_cnt}.\n"
    return head + "\n".join(res[:30])  # ограничим вывод


def cmd_week(all_rows: List[Dict[str, Any]]) -> str:
    today = date.today()
    till = today + timedelta(days=7)
    res = []
    status_stats: Dict[str, int] = {}
    for r in all_rows:
        sd = _to_date(r.get("ship_date", ""))
        ad = _to_date(r.get("arrival", ""))
        in_range = False
        if sd and today <= sd <= till:
            in_range = True
        if ad and today <= ad <= till:
            in_range = True
        if in_range:
            res.append(short_line(r))
            st = _norm(r.get("status"))
            if st:
                status_stats[st] = status_stats.get(st, 0) + 1

    if not res:
        return f"В ближайшие 7 дней ({dt_ru(today)}–{dt_ru(till)}) заявок нет."
    # собираем сводку по статусам (в человекочитаемом виде)
    top = sorted(status_stats.items(), key=lambda x: -x[1])
    stats_str = ", ".join([f"{k}: {v}" for k, v in top[:6]])
    head = f"На неделе {len(res)} заявок. Статусы: {stats_str or '—'}.\n"
    return head + "\n".join(res[:40])


def cmd_my(all_rows: List[Dict[str, Any]], arg_name: str, user: Dict[str, Any]) -> str:
    # имя из аргумента или пытаемся угадать по профилю
    query = arg_name.strip()
    if not query:
        query = pick_applicant_from_user(user)
    if not query:
        return "Не понял, по кому искать. Укажи имя: /my Иванов"

    qn = _norm(query)
    mine = [r for r in all_rows if qn in _norm(r.get("applicant"))]
    if not mine:
        return f'Заявок для "{html.escape(query)}" не найдено.'

    # короткая сводка по статусам
    status_stats: Dict[str, int] = {}
    for r in mine:
        st = _norm(r.get("status"))
        if st:
            status_stats[st] = status_stats.get(st, 0) + 1
    stats_str = ", ".join([f"{k}: {v}" for k, v in sorted(status_stats.items(), key=lambda x: -x[1])])

    head = f'Заявки для "{html.escape(query)}": {len(mine)} шт. ({stats_str or "без статусов"}).\n'
    lines = [short_line(r) for r in mine[:40]]
    return head + "\n".join(lines)


def cmd_priority(all_rows: List[Dict[str, Any]]) -> str:
    crit = [r for r in all_rows if _norm(r.get("priority")).startswith("авар")]
    if not crit:
        return "Аварийных заявок нет."
    head = f"Аварийные заявки: {len(crit)}.\n"
    return head + "\n".join([short_line(r) for r in crit[:40]])


# ======== ВЕБХУК ИЗ TELEGRAM (/tg) ========
@app.post("/tg")
async def telegram_webhook(req: Request):
    upd = await req.json()
    # chat id + user + text
    msg = upd.get("message") or upd.get("edited_message")
    if not msg:
        return {"ok": True}

    chat_id = str(msg["chat"]["id"])
    user = msg.get("from", {}) or {}
    text = msg.get("text", "") or ""

    # бот-ник (чтобы резать /cmd@BotName)
    me = requests.get(f"{TG_API}/getMe", timeout=10).json()
    bot_username = (me.get("result") or {}).get("username") or ""

    cmd, arg = parse_command(text, bot_username)
    if not cmd:
        return {"ok": True}  # игнорим обычные сообщения

    # простые команды без данных
    if cmd in ("/start",):
        return tg_send_message(chat_id, HELP_TEXT)

    if cmd in ("/help",):
        return tg_send_message(chat_id, HELP_TEXT)

    if cmd in ("/id",):
        uid = user.get("id")
        uname = user.get("username")
        full = " ".join([x for x in [user.get("first_name"), user.get("last_name")] if x]).strip()
        text = f"Ваш ID: <b>{uid}</b>\nПользователь: @{html.escape(uname)}\nИмя: {html.escape(full)}"
        return tg_send_message(chat_id, text)

    # команды, которые требуют таблицу
    try:
        rows = load_rows()
    except Exception as e:
        return tg_send_message(chat_id, f"Не удалось загрузить таблицу: {html.escape(str(e))}")

    if cmd == "/today":
        return tg_send_message(chat_id, cmd_today(rows))

    if cmd == "/week":
        return tg_send_message(chat_id, cmd_week(rows))

    if cmd == "/my":
        return tg_send_message(chat_id, cmd_my(rows, arg, user))

    if cmd == "/priority":
        return tg_send_message(chat_id, cmd_priority(rows))

    # неизвестная команда
    return tg_send_message(chat_id, "Не знаю такую команду. Напиши /help")
