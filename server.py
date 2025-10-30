import os
import requests
from typing import Optional, Dict, Any
from fastapi import FastAPI, Request, Header, HTTPException

app = FastAPI(title="SnabOrders Bot", version="2.3")

# ===== ENV =====
BOT_TOKEN        = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID          = os.getenv("CHAT_ID", "").strip()          # -100... (id группы)
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "").strip()   # общий секрет для /notify
SHEET_SCRIPT_URL = os.getenv("SHEET_SCRIPT_URL", "").strip() # URL веб-приложения Apps Script
SHEET_API_KEY    = os.getenv("SHEET_API_KEY", "").strip()    # ключ для вызова Apps Script

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# --- ЕДИНЫЕ КОНСТАНТЫ СТАТУСОВ ---
STATUS_WORK     = "В РАБОТУ"
STATUS_REVISE   = "НА ДОРАБОТКУ"
STATUS_REJECT   = "ОТКЛОНЕНО"
STATUS_RECEIVED = "Доставлено"

# user_id -> order_id  (для режима «На доработку»)
PENDING_REVISE: Dict[int, str] = {}

# ---------- утилиты TG ----------
def tg_send_message(text: str, reply_markup: Optional[Dict]=None, parse_mode: str="HTML"):
    if not BOT_TOKEN or not CHAT_ID:
        print("TG: missing BOT_TOKEN/CHAT_ID")
        return
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        r = requests.post(f"{TG_API}/sendMessage", json=payload, timeout=10)
        print("tg_send_message:", r.status_code, r.text[:200])
    except Exception as e:
        print("tg_send_message error:", e)

def tg_edit_reply_markup(chat_id: int, message_id: int, reply_markup: Optional[Dict]):
    try:
        r = requests.post(f"{TG_API}/editMessageReplyMarkup",
                          json={"chat_id": chat_id, "message_id": message_id,
                                "reply_markup": reply_markup},
                          timeout=10)
        print("tg_edit_reply_markup:", r.status_code, r.text[:200])
    except Exception as e:
        print("tg_edit_reply_markup error:", e)

def fmt_user(u: Dict[str, Any]) -> str:
    uname = u.get("username")
    if uname:
        return f"@{uname}"
    first = (u.get("first_name") or "").strip()
    last  = (u.get("last_name")  or "").strip()
    full = (first + " " + last).strip()
    return full or f"id:{u.get('id')}"

# ---------- утилиты Sheet ----------
def sheet_update_status(order_id: str, new_status: str, comment: Optional[str]=None):
    if not SHEET_SCRIPT_URL or not SHEET_API_KEY:
        print("SHEET: missing SHEET_SCRIPT_URL/SHEET_API_KEY")
        return {"ok": False, "error": "config"}
    payload = {"action": "update_status", "order_id": order_id, "new_status": new_status}
    if comment is not None:
        payload["comment"] = comment
        payload["action"] = "status_with_comment"
    try:
        r = requests.post(SHEET_SCRIPT_URL,
                          headers={"Authorization": f"Bearer {SHEET_API_KEY}"},
                          json=payload, timeout=12)
        print("sheet_update_status:", r.status_code, r.text[:200])
        return r.json() if r.headers.get("content-type","").startswith("application/json") else {"ok": r.ok}
    except Exception as e:
        print("sheet_update_status error:", e)
        return {"ok": False, "error": str(e)}

# ---------- клавиатуры ----------
def kb_delivered(order_id: str) -> Dict:
    # одна кнопка в отдельной строке (вертикально)
    return {"inline_keyboard": [[{"text": "📦 ТМЦ ПОЛУЧЕНО", "callback_data": f"received|{order_id}"}]]}

def kb_approval(order_id: str) -> Dict:
    # вертикально: по одной в строке
    return {
        "inline_keyboard": [
            [{"text": "✅ В РАБОТУ",     "callback_data": f"approve|{order_id}"}],
            [{"text": "🔧 НА ДОРАБОТКУ", "callback_data": f"revise|{order_id}"}],
            [{"text": "❌ ОТКЛОНЕНО",    "callback_data": f"reject|{order_id}"}],
        ]
    }

def norm(s: str) -> str:
    return (s or "").lower().replace("\u00a0", " ").strip()

# ---------- формат уведомления ----------
def make_message(data: Dict[str, Any]) -> str:
    get = lambda k: (data.get(k) or "").strip()
    lines = ["📦 <b>Уведомление о заявке</b>"]
    if get("order_id"):   lines.append(f"🧾 <b>Заявка:</b> {get('order_id')}")
    if get("priority"):   lines.append(f"⭐ <b>Приоритет:</b> {get('priority')}")
    if get("status"):     lines.append(f"🚚 <b>Статус:</b> {get('status')}")
    if get("carrier"):    lines.append(f"🚛 <b>ТК:</b> {get('carrier')}")
    if get("ttn"):        lines.append(f"📄 <b>№ ТТН:</b> {get('ttn')}")
    if get("ship_date"):  lines.append(f"📅 <b>Дата отгрузки:</b> {get('ship_date')}")
    if get("arrival"):    lines.append(f"📅 <b>Дата прибытия:</b> {get('arrival')}")
    if get("applicant"):  lines.append(f"👤 <b>Заявитель:</b> {get('applicant')}")
    if get("comment"):    lines.append(f"📝 <b>Комментарий:</b> {get('comment')}")
    return "\n".join(lines)

def pick_keyboard(data: Dict[str, Any]) -> Optional[Dict]:
    st = norm(data.get("status",""))
    cm = norm(data.get("comment",""))
    # кнопка "получено" только при статусе «доставлено в тк»
    if "доставлено в тк" in st:
        return kb_delivered(data.get("order_id",""))
    # согласование, если в комментарии требование согласования
    if "требуется согласование" in cm:
        return kb_approval(data.get("order_id",""))
    return None

# ---------- ROUTES ----------
@app.get("/")
def root():
    return {"ok": True, "routes": ["/", "/health", "/notify", "/tg"]}

@app.get("/health")
def health():
    return {"ok": True, "service": "snaborders-bot"}

# Google Apps Script шлёт сюда изменения строки
@app.post("/notify")
async def notify(req: Request, authorization: Optional[str] = Header(None)):
    if authorization != f"Bearer {WEBHOOK_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")
    data = await req.json()
    text = make_message(data)
    kb   = pick_keyboard(data)
    tg_send_message(text, reply_markup=kb)
    return {"ok": True}

# Telegram webhook
@app.post("/tg")
async def tg_webhook(req: Request):
    upd = await req.json()
    print("TG update:", str(upd)[:800])

    # нажата инлайн-кнопка
    if "callback_query" in upd:
        cq   = upd["callback_query"]
        user = cq.get("from", {})
        chat = cq.get("message", {}).get("chat", {})
        mid  = cq.get("message", {}).get("message_id")
        data = (cq.get("data") or "")
        parts = data.split("|", 1)
        if len(parts) != 2:
            return {"ok": True}
        action, order_id = parts[0], parts[1]
        who = fmt_user(user)

        # отключаем клавиатуру у исходного сообщения
        try:
            tg_edit_reply_markup(chat_id=chat["id"], message_id=mid, reply_markup=None)
        except Exception as e:
            print("remove kb error:", e)

        if action == "received":
            # Получение ТМЦ → ставим "Доставлено"
            sheet_update_status(order_id, STATUS_RECEIVED)
            tg_send_message(f"📦 <b>ТМЦ ПОЛУЧЕНО</b> по заявке <b>{order_id}</b>.\nНажал: {who}")

        elif action == "approve":
            # Согласование → ставим "В РАБОТУ"
            sheet_update_status(order_id, STATUS_WORK)
            tg_send_message(f"✅ <b>В РАБОТУ</b> по заявке <b>{order_id}</b>.\nНажал: {who}")

        elif action == "reject":
            # Отклонено → ставим "ОТКЛОНЕНО"
            sheet_update_status(order_id, STATUS_REJECT)
            tg_send_message(f"❌ <b>ОТКЛОНЕНО</b> по заявке <b>{order_id}</b>.\nНажал: {who}")

        elif action == "revise":
            # Ждём текст от нажавшего → потом проставим "НА ДОРАБОТКУ" + комментарий
            PENDING_REVISE[user.get("id")] = order_id
            tg_send_message(
                f"🔧 <b>НА ДОРАБОТКУ</b> по заявке <b>{order_id}</b>.\n"
                f"{who}, отправьте одним сообщением комментарий — он попадёт в таблицу."
            )
        return {"ok": True}

    # текст от пользователя — это комментарий к «На доработку»
    if "message" in upd:
        msg = upd["message"]
        uid = msg.get("from", {}).get("id")
        text = (msg.get("text") or "").strip()
        if uid in PENDING_REVISE and text:
            order_id = PENDING_REVISE.pop(uid)
            # Ставим "НА ДОРАБОТКУ" и записываем комментарий
            sheet_update_status(order_id, STATUS_REVISE, comment=text)
            tg_send_message(
                f"🔧 <b>НА ДОРАБОТКУ</b> по заявке <b>{order_id}</b>.\nКомментарий: {text}"
            )
        return {"ok": True}

    return {"ok": True}
