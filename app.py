# === BEGIN: COMMANDS ADDON (для ответов в группе) ===
import os, io, csv, time, html
import typing as T
from datetime import datetime, timedelta, date

import requests
from fastapi import Request, HTTPException

BOT_TOKEN        = os.getenv("BOT_TOKEN", "").strip()
SHEET_CSV_URL    = os.getenv("SHEET_CSV_URL", "").strip()
SHEET_CACHE_TTL  = int(os.getenv("SHEET_CACHE_TTL", "120"))
PUBLIC_URL       = os.getenv("PUBLIC_URL", "").strip()  # https://your.koyeb.app
TELEGRAM_API     = f"https://api.telegram.org/bot{BOT_TOKEN}"

# соответствие колонок в листе (заголовки 1-й строки)
FIELD_MAP = {
    "order":       "Заявка",
    "priority":    "Приоритет",
    "status":      "Статус",
    "ship_date":   "Дата Отгрузки",
    "arrive_date": "Дата Прибытия",
    "carrier":     "ТК",
    "ttn":         "№ ТТН",
    "applicant":   "Заявитель",
}

# простой кэш CSV
_csv_cache = {"ts": 0.0, "rows": []}

def _parse_date(s: str) -> T.Optional[date]:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y.%m.%d"):
        try: return datetime.strptime(s, fmt).date()
        except: pass
    try: return datetime.fromisoformat(s).date()
    except: return None

def _fmt_date_ru(d: T.Optional[date]) -> str:
    if not d: return ""
    m = ["января","февраля","марта","апреля","мая","июня","июля","августа","сентября","октября","ноября","декабря"]
    return f"{d.day} {m[d.month-1]} {d.year}"

def load_rows() -> T.List[dict]:
    now = time.time()
    if _csv_cache["rows"] and now - _csv_cache["ts"] < SHEET_CACHE_TTL:
        return _csv_cache["rows"]
    if not SHEET_CSV_URL:
        return []
    r = requests.get(SHEET_CSV_URL, timeout=25)
    r.raise_for_status()
    reader = csv.DictReader(io.StringIO(r.text))
    rows = []
    for raw in reader:
        row = {k: (raw.get(v, "") or "").strip() for k, v in FIELD_MAP.items()}
        row["_ship"]   = _parse_date(row["ship_date"])
        row["_arrive"] = _parse_date(row["arrive_date"])
        rows.append(row)
    _csv_cache["rows"] = rows
    _csv_cache["ts"] = now
    return rows

def tg_send(chat_id: int | str, text: str, reply_to: int | None = None):
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if reply_to: data["reply_to_message_id"] = reply_to
    resp = requests.post(f"{TELEGRAM_API}/sendMessage", json=data, timeout=20)
    print("TG SEND:", resp.status_code, resp.text[:300])
    return resp.ok

def _row_card(r: dict) -> str:
    e = lambda s: html.escape(s or "")
    parts = []
    if r["order"]:       parts.append(f"🧾 <b>Заявка:</b> {e(r['order'])}")
    if r["priority"]:    parts.append(f"⭐ <b>Приоритет:</b> {e(r['priority'])}")
    if r["status"]:      parts.append(f"🚚 <b>Статус:</b> {e(r['status'])}")
    if r["_ship"]:       parts.append(f"📅 <b>Дата отгрузки:</b> {_fmt_date_ru(r['_ship'])}")
    if r["_arrive"]:     parts.append(f"📦 <b>Дата прибытия:</b> {_fmt_date_ru(r['_arrive'])}")
    if r["carrier"]:     parts.append(f"🚛 <b>ТК:</b> {e(r['carrier'])}")
    if r["ttn"]:         parts.append(f"📄 <b>№ ТТН:</b> {e(r['ttn'])}")
    if r["applicant"]:   parts.append(f"👤 <b>Заявитель:</b> {e(r['applicant'])}")
    return "\n".join(parts)

def _list_short(rows: T.List[dict], limit: int = 10) -> str:
    if not rows: return "Ничего не найдено."
    out = []
    for r in rows[:limit]:
        z = html.escape(r["order"])
        st = html.escape(r["status"])
        pr = html.escape(r["priority"])
        ship = _fmt_date_ru(r["_ship"])
        tk = html.escape(r["carrier"])
        ttn = html.escape(r["ttn"])
        out.append(f"• <b>{z}</b>\n  ⭐ {pr} | 🚚 {st}\n  📅 Отгр.: {ship} | 🚛 {tk} | 📄 {ttn}")
    return "\n\n".join(out)

def _cmd_help() -> str:
    return (
        "🤖 <b>Команды</b>\n"
        "/my — мои заявки (ищет вас в «Заявитель»)\n"
        "/status [текст] — заявки с этим статусом (напр.: /status В пути)\n"
        "/today — отгрузки сегодня\n"
        "/week — отгрузки на этой неделе\n"
        "/priority — аварийные/приоритетные\n"
        "/search <текст> — поиск по названию заявки\n"
        "/last — последние отгрузки\n"
        "/id — ваш Telegram ID"
    )

@app.post("/tg")
async def tg_webhook(req: Request):
    if not BOT_TOKEN:
        raise HTTPException(500, "BOT_TOKEN not set")
    upd = await req.json()
    print("UPDATE:", json.dumps(upd, ensure_ascii=False)[:1000])

    msg = upd.get("message") or upd.get("edited_message") or upd.get("channel_post")
    if not msg: return {"ok": True}
    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    user = msg.get("from") or {}
    username = (user.get("username") or "").lower()
    full_name = " ".join([user.get("first_name",""), user.get("last_name","")]).strip().lower()

    if not text.startswith("/"):
        return {"ok": True}

    cmd, *rest = text.split(maxsplit=1)
    arg = rest[0].strip() if rest else ""
    cmd = cmd.split("@")[0].lower()

    rows = load_rows()

    if cmd in ("/start", "/help"):
        tg_send(chat_id, _cmd_help()); return {"ok": True}

    if cmd == "/id":
        tg_send(chat_id, f"🪪 Ваш Telegram ID: <code>{user.get('id')}</code>"); return {"ok": True}

    if cmd == "/my":
        flt = []
        for r in rows:
            a = (r["applicant"] or "").lower()
            uok = username and (a == f"@{username}" or username in a)
            nok = (not username) and full_name and full_name in a
            if uok or nok: flt.append(r)
        tg_send(chat_id, "🙋 <b>Ваши заявки</b>\n\n" + _list_short(flt)); return {"ok": True}

    if cmd == "/status":
        if not arg:
            from collections import Counter
            c = Counter([(r["status"] or "—") for r in rows])
            top = "\n".join([f"• <b>{html.escape(k)}</b>: {v}" for k, v in c.most_common(10)])
            tg_send(chat_id, "📊 <b>Распределение по статусам</b>\n" + top + "\n\nНапример: /status В пути")
            return {"ok": True}
        flt = [r for r in rows if (r["status"] or "").lower() == arg.lower()]
        tg_send(chat_id, f"📌 <b>Статус: {html.escape(arg)}</b>\n\n" + _list_short(flt)); return {"ok": True}

    if cmd == "/today":
        today = date.today()
        flt = [r for r in rows if r["_ship"] == today]
        tg_send(chat_id, f"📅 <b>Отгрузки сегодня</b>\n\n" + _list_short(flt)); return {"ok": True}

    if cmd == "/week":
        today = date.today()
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        flt = [r for r in rows if r["_ship"] and start <= r["_ship"] <= end]
        tg_send(chat_id, f"🗓 <b>Отгрузки на неделе</b>\n\n" + _list_short(flt)); return {"ok": True}

    if cmd == "/priority":
        keys = {"аварийно", "аварийный", "высокий", "приоритетно", "приоритетный"}
        flt = [r for r in rows if (r["priority"] or "").lower() in keys]
        tg_send(chat_id, "⚠️ <b>Аварийные / приоритетные</b>\n\n" + _list_short(flt)); return {"ok": True}

    if cmd == "/search":
        if not arg: tg_send(chat_id, "🔎 Укажи текст: /search фильтр"); return {"ok": True}
        q = arg.lower()
        flt = [r for r in rows if q in (r["order"] or "").lower()]
        tg_send(chat_id, f"🔎 <b>Поиск:</b> {html.escape(arg)}\n\n" + _list_short(flt)); return {"ok": True}

    if cmd == "/last":
        flt = [r for r in rows if r["_ship"]]
        flt.sort(key=lambda x: x["_ship"], reverse=True)
        tg_send(chat_id, "🕒 <b>Последние отгрузки</b>\n\n" + _list_short(flt)); return {"ok": True}

    tg_send(chat_id, "Неизвестная команда. /help"); return {"ok": True}
# === END: COMMANDS ADDON ===
