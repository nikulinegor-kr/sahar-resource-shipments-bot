import os, json, html, requests
from fastapi import FastAPI, Request, Header, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any

# === ENV ===
BOT_TOKEN        = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID          = os.getenv("CHAT_ID", "").strip()
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "").strip()
SHEET_CSV_URL    = os.getenv("SHEET_CSV_URL", "").strip()          # CSV публикация (для /today, /week и т.п., если понадобится)
SHEET_SCRIPT_URL = os.getenv("SHEET_SCRIPT_URL", "").strip()        # URL веб-приложения Apps Script (doPost)

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

app = FastAPI(title="Snab Notify Bot", version="1.2.0")

# === модель входящих уведомлений из таблицы ===
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

# === TG helpers ===
def tg_send(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not BOT_TOKEN:
        return {"ok": False, "error": "BOT_TOKEN missing"}
    try:
        r = requests.post(f"{TG_API}/{method}", json=payload, timeout=10)
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}

def tg_send_message(text: str, reply_markup: Optional[Dict[str, Any]] = None, parse_mode: str = "HTML") -> Dict[str, Any]:
    if not CHAT_ID:
        return {"ok": False, "error": "CHAT_ID missing"}
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg_send("sendMessage", payload)

def tg_answer_callback_query(callback_query_id: str, text: str = "", show_alert: bool = False):
    return tg_send("answerCallbackQuery", {
        "callback_query_id": callback_query_id,
        "text": text,
        "show_alert": show_alert
    })

# === формат уведомления ===
def format_order_text(data: Dict[str, Any]) -> str:
    g = lambda k: (data.get(k) or "").strip()
    lines = ["📦 Уведомление о заявке"]
    lines.append(f"🧾 Заявка: {html.escape(g('order_id') or '—')}")
    if g("priority"):   lines.append(f"⭐ Приоритет: {html.escape(g('priority'))}")
    if g("status"):     lines.append(f"🚚 Статус: {html.escape(g('status'))}")
    if g("carrier"):    lines.append(f"🚛 ТК: {html.escape(g('carrier'))}")
    if g("ttn"):        lines.append(f"📄 № ТТН: {html.escape(g('ttn'))}")
    if g("ship_date"):  lines.append(f"📅 Дата отгрузки: {html.escape(g('ship_date'))}")
    if g("arrival"):    lines.append(f"📅 Дата прибытия: {html.escape(g('arrival'))}")
    if g("applicant"):  lines.append(f"👤 Заявитель: {html.escape(g('applicant'))}")
    if g("comment"):    lines.append(f"📝 Комментарий: {html.escape(g('comment'))}")
    return "\n".join(lines)

def need_received_button(status: str) -> bool:
    s = (status or "").strip().lower()
    # показываем кнопку только если статус именно «доставлено в тк»
    return s == "доставлено в тк"

def build_received_keyboard(order_id: str) -> Dict[str, Any]:
    # callback_data — компактный JSON
    data = json.dumps({"a":"received","order_id": order_id}, ensure_ascii=False)
    return {
        "inline_keyboard": [[
            {"text": "✅ ТМЦ ПОЛУЧЕНО", "callback_data": data}
        ]]
    }

# === служебные роуты ===
@app.get("/")
def root():
    return {"ok": True, "routes": ["/", "/health", "/tg (GET/POST)", "/notify", "/docs"]}

@app.get("/health")
def health():
    return {"ok": True, "service": "snab-bot", "webhook": "/tg"}

@app.get("/tg")
def get_tg():
    return {"ok": True, "route": "/tg"}

# === приём из таблицы ===
@app.post("/notify")
async def notify(req: Request, authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization.split("Bearer ")[-1].strip()
    if token != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid token")

    data = await req.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Body must be JSON object")

    text = format_order_text(data)
    kb = None
    if need_received_button(data.get("status", "")) and data.get("order_id"):
        kb = build_received_keyboard(data["order_id"])

    res = tg_send_message(text, reply_markup=kb)
    return {"ok": True, "telegram_response": res}

# === Telegram webhook: обрабатываем callback кнопки ===
@app.post("/tg")
async def telegram_webhook(req: Request):
    update = await req.json()
    # интересует только callback_query
    if "callback_query" in update:
        cq = update["callback_query"]
        cqid = cq.get("id")
        from_user = cq.get("from", {})
        data_raw = cq.get("data") or ""
        try:
            data = json.loads(data_raw)
        except Exception:
            data = {}

        if data.get("a") == "received" and data.get("order_id") and SHEET_SCRIPT_URL:
            # шлём в Apps Script «поставить статус Доставлено»
            try:
                r = requests.post(SHEET_SCRIPT_URL, json={
                    "action": "set_status",
                    "order_id": data["order_id"],
                    "status": "Доставлено"
                }, timeout=10)
                # ответим пользователю
                if cqid:
                    ok = r.status_code >= 200 and r.status_code < 300
                    tg_answer_callback_query(cqid, "Статус обновлён" if ok else "Не удалось обновить статус")
            except Exception as e:
                if cqid:
                    tg_answer_callback_query(cqid, "Ошибка связи с Google Script", show_alert=True)

    return {"ok": True, "received": True}

# локальный запуск
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000)
