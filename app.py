# app.py
import os, html, csv, io, requests, datetime as dt
from typing import Optional, List
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

app = FastAPI(title="Snab Notify", version="1.1.0")

# === ENV ===
BOT_TOKEN       = os.getenv("BOT_TOKEN", "")                   # 8436...Rg
DEFAULT_CHAT_ID = os.getenv("CHAT_ID", "")                     # -100...
WEBHOOK_SECRET  = os.getenv("WEBHOOK_SECRET", "")              # sahar2025secure_longtoken
SHEET_CSV_URL   = os.getenv("SHEET_CSV_URL", "")               # опубликованный CSV "Заявки 2025"

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# === MODELS (для /notify как было) ===
class Responsible(BaseModel):
    name: Optional[str] = None
    username: Optional[str] = None  # без @
    user_id: Optional[int] = None

class Item(BaseModel):
    name: str
    qty: Optional[float] = None
    unit: Optional[str] = None

class NotifyPayload(BaseModel):
    order_id: str = Field(..., description="Номер заявки/заказа")
    recipient: str = Field(..., description="Получатель (компания)")
    priority: Optional[str] = None
    city: Optional[str] = None
    phone: Optional[str] = None
    ship_date: Optional[str] = None
    arrival_date: Optional[str] = None
    carrier: Optional[str] = None
    ttn: Optional[str] = None
    status: Optional[str] = None
    applicant: Optional[str] = None
    comment: Optional[str] = None
    responsible: Optional[Responsible] = None
    items: List[Item] = []

# === TELEGRAM helpers ===
def tg_send(chat_id: str, text: str, parse: str = None):
    data = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if parse:
        data["parse_mode"] = parse
    r = requests.post(f"{TG_API}/sendMessage", json=data, timeout=20)
    print("TG SEND ->", r.status_code, r.text)
    return r.ok

def tg_reply_from_message(message: dict, text: str, parse: str = None):
    chat_id = str(message["chat"]["id"])
    return tg_send(chat_id, text, parse)

# === CSV helpers (для команд /my /status /today /week и т.д.) ===
DATE_COL_SHIP = "Дата Отгрузки"
DATE_COL_ARR  = "Дата/Д"
COLS = {
    "order": "Заявка",
    "priority": "Приоритет",
    "status": "Статус",
    "tk": "ТК",
    "ttn": "№ ТТН",
    "applicant": "Заявитель",
}

def load_rows() -> List[dict]:
    """Читаем опубликованный CSV Google Sheets (лист 'Заявки 2025')."""
    if not SHEET_CSV_URL:
        return []
    rs = requests.get(SHEET_CSV_URL, timeout=25)
    rs.raise_for_status()
    text = rs.content.decode("utf-8", errors="ignore")
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)

def parse_date_ru(s: str) -> Optional[dt.date]:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%d.%m.%y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except Exception:
            pass
    return None

# === Рендер уведомления (/notify) ===
def render_message(p: NotifyPayload) -> str:
    esc = lambda s: html.escape(s or "")
    lines = ["<b>📦 Уведомление о заявке</b>", ""]
    if p.order_id:      lines.append(f"🧾 <b>Заявка:</b> {esc(p.order_id)}")
    if p.priority:      lines.append(f"⭐ <b>Приоритет:</b> {esc(p.priority)}")
    if p.status:        lines.append(f"🚚 <b>Статус:</b> {esc(p.status)}")
    if p.ship_date:     lines.append(f"📅 <b>Дата отгрузки:</b> {esc(p.ship_date)}")
    if p.arrival_date:  lines.append(f"📦 <b>Дата прибытия:</b> {esc(p.arrival_date)}")
    if p.carrier:       lines.append(f"🚛 <b>ТК:</b> {esc(p.carrier)}")
    if p.ttn:           lines.append(f"📄 <b>№ ТТН:</b> {esc(p.ttn)}")
    if p.applicant:     lines.append(f"👤 <b>Заявитель:</b> {esc(p.applicant)}")
    if p.comment: 
        lines += ["", esc(p.comment)]
    # Ответственный (опционально)
    if p.responsible:
        r = p.responsible
        if r.username: lines.append(f"👤 <b>Ответственный:</b> @{esc(r.username)}")
        elif r.user_id: lines.append(f"👤 <b>Ответственный:</b> tg://user?id={r.user_id}")
        elif r.name:   lines.append(f"👤 <b>Ответственный:</b> {esc(r.name)}")
    return "\n".join(lines)

# === HEALTH ===
@app.get("/health")
def health():
    return {"ok": True}

# === NOTIFY (как было) ===
@app.post("/notify")
def notify(payload: NotifyPayload, authorization: str = Header(default="")):
    if WEBHOOK_SECRET and authorization != f"Bearer {WEBHOOK_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    msg = render_message(payload)
    chat_id = DEFAULT_CHAT_ID or ""  # если нужно слать всегда в канал
    if not chat_id:
        raise HTTPException(status_code=400, detail="CHAT_ID is not set")

    ok = tg_send(chat_id, msg, parse="HTML")
    if not ok:
        raise HTTPException(status_code=502, detail="Telegram send failed")
    return {"ok": True}

# === TELEGRAM WEBHOOK: НОВЫЙ ЭНДПОИНТ ===
HELP_TEXT = (
    "🤖 <b>Команды:</b>\n"
    "/help — список команд\n"
    "/my — мои заявки (по Заявителю)\n"
    "/status — сводка по статусам\n"
    "/today — отгрузки сегодня\n"
    "/week — отгрузки на 7 дней\n"
    "/priority — аварийные заявки\n"
    "/last — последние обновления\n"
    "/id — показать ваш Telegram ID\n"
)

@app.post("/tg")
async def telegram_webhook(req: Request):
    upd = await req.json()
    print("UPDATE:", upd)

    msg = upd.get("message") or upd.get("channel_post")
    if not msg:
        # изменения участника чата и прочее — просто подтверждаем
        return {"ok": True}

    chat_id = str(msg["chat"]["id"])
    text = (msg.get("text") or "").strip()

    # /id
    if text.startswith("/id"):
        uid = msg["from"]["id"]
        tg_reply_from_message(msg, f"Ваш Telegram ID: <code>{uid}</code>", "HTML")
        return {"ok": True}

    # /start, /help
    if text.startswith("/start") or text.startswith("/help"):
        tg_reply_from_message(msg, HELP_TEXT, "HTML")
        return {"ok": True}

    # Дальше команды, требующие данных из таблицы
    rows = []
    try:
        rows = load_rows()
    except Exception as e:
        tg_reply_from_message(msg, f"Не удалось получить данные из таблицы:\n{e}")
        return {"ok": True}

    # Нормализуем имена колонок
    def col(row, title): 
        return (row.get(title) or "").strip()

    # /status — подсчёт по статусам
    if text.startswith("/status"):
        from collections import Counter
        c = Counter(col(r, COLS["status"]) for r in rows if col(r, COLS["status"]))
        if not c:
            tg_reply_from_message(msg, "Статусов нет.")
            return {"ok": True}
        lines = ["📊 <b>Сводка по статусам</b>:", ""]
        for k, v in c.most_common():
            lines.append(f"• {k}: {v}")
        tg_reply_from_message(msg, "\n".join(lines), "HTML")
        return {"ok": True}

    # /priority — показать аварийные
    if text.startswith("/priority"):
        pr_list = [r for r in rows if col(r, COLS["priority"]).lower().startswith("авар")]
        if not pr_list:
            tg_reply_from_message(msg, "Аварийных заявок нет.")
            return {"ok": True}
        top = pr_list[:10]
        out = ["⚠️ <b>Аварийные заявки (топ-10):</b>", ""]
        for r in top:
            out.append(f"• {col(r, COLS['order'])} — {col(r, COLS['status'])}")
        tg_reply_from_message(msg, "\n".join(out), "HTML")
        return {"ok": True}

    # /today — отгрузки сегодня
    if text.startswith("/today"):
        today = dt.date.today()
        res = []
        for r in rows:
            d = parse_date_ru(col(r, DATE_COL_SHIP))
            if d == today:
                res.append(r)
        if not res:
            tg_reply_from_message(msg, "Сегодня отгрузок нет.")
            return {"ok": True}
        out = ["📅 <b>Отгрузки сегодня:</b>", ""]
        for r in res[:15]:
            out.append(f"• {col(r, COLS['order'])} — {col(r, COLS['status'])} — {col(r, COLS['tk'])}")
        tg_reply_from_message(msg, "\n".join(out), "HTML")
        return {"ok": True}

    # /week — ближайшие 7 дней
    if text.startswith("/week"):
        today = dt.date.today()
        until = today + dt.timedelta(days=7)
        res = []
        for r in rows:
            d = parse_date_ru(col(r, DATE_COL_SHIP))
            if d and today <= d <= until:
                res.append((d, r))
        if not res:
            tg_reply_from_message(msg, "Отгрузок на этой неделе нет.")
            return {"ok": True}
        res.sort(key=lambda x: x[0])
        out = ["🗓️ <b>Отгрузки в ближайшие 7 дней:</b>", ""]
        for d, r in res[:20]:
            out.append(f"• {d.isoformat()} — {col(r, COLS['order'])} — {col(r, COLS['status'])}")
        tg_reply_from_message(msg, "\n".join(out), "HTML")
        return {"ok": True}

    # /my — по «Заявителю»
    if text.startswith("/my"):
        u = msg["from"]
        candidates = set(filter(None, [
            u.get("username"),
            u.get("first_name"),
            u.get("last_name"),
            f"{u.get('first_name','')} {u.get('last_name','')}".strip(),
        ]))
        res = []
        for r in rows:
            who = col(r, COLS["applicant"])
            if not who:
                continue
            for c in candidates:
                if c and c.lower() in who.lower():
                    res.append(r)
                    break
        if not res:
            tg_reply_from_message(msg, "Заявок по вам не найдено.")
            return {"ok": True}
        out = ["👤 <b>Ваши заявки:</b>", ""]
        for r in res[:15]:
            out.append(f"• {col(r, COLS['order'])} — {col(r, COLS['status'])} — {col(r, DATE_COL_SHIP)}")
        tg_reply_from_message(msg, "\n".join(out), "HTML")
        return {"ok": True}

    # /last — просто последние N строк таблицы
    if text.startswith("/last"):
        N = 10
        res = rows[-N:]
        if not res:
            tg_reply_from_message(msg, "Данных нет.")
            return {"ok": True}
        out = ["🕘 <b>Последние обновления:</b>", ""]
        for r in res:
            out.append(f"• {col(r, COLS['order'])} — {col(r, COLS['status'])} — {col(r, DATE_COL_SHIP)}")
        tg_reply_from_message(msg, "\n".join(out), "HTML")
        return {"ok": True}

    # нераспознанная команда
    tg_reply_from_message(msg, "Не понимаю команду. Напишите /help")
    return {"ok": True}
