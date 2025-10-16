# server.py
import os, html, json, requests
from typing import Optional, Dict, Any
from fastapi import FastAPI, Request, Header, HTTPException

BOT_TOKEN        = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID          = os.getenv("CHAT_ID", "").strip()
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "").strip()     # должен совпадать с CFG.SECRET
SHEET_SCRIPT_URL = os.getenv("SHEET_SCRIPT_URL", "").strip()   # URL веб-приложения Apps Script .../exec
SHEET_API_KEY    = os.getenv("SHEET_API_KEY", "").strip()      # тот же секрет

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

app = FastAPI(title="Snab Notify Bot", version="1.2.0")

def tg_send_message(text: str, kb: Optional[dict] = None) -> Dict[str, Any]:
  if not BOT_TOKEN or not CHAT_ID:
    return {"ok": False, "error": "BOT_TOKEN or CHAT_ID missing"}
  url = f"{TG_API}/sendMessage"
  payload = {
    "chat_id": CHAT_ID,
    "text": text,
    "parse_mode": "HTML",
    "disable_web_page_preview": True,
  }
  if kb:
    payload["reply_markup"] = kb
  r = requests.post(url, json=payload, timeout=15)
  return r.json()

def tg_edit_markup(chat_id: int, message_id: int, kb: Optional[dict]) -> Dict[str, Any]:
  url = f"{TG_API}/editMessageReplyMarkup"
  payload = {"chat_id": chat_id, "message_id": message_id}
  if kb is not None:
    payload["reply_markup"] = kb
  r = requests.post(url, json=payload, timeout=10)
  return r.json()

def tg_answer_callback(cb_id: str, text: str, alert: bool=False):
  url = f"{TG_API}/answerCallbackQuery"
  requests.post(url, json={"callback_query_id": cb_id, "text": text, "show_alert": alert}, timeout=10)

def format_order_text(d: Dict[str, Any]) -> str:
  g = lambda k: (d.get(k) or "").strip()
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

def ready_for_receive(status: str) -> bool:
  s = (status or "").lower()
  return ("доставлено" in s) and ("тк" in s)   # показываем кнопку только при «Доставлено в ТК»

@app.get("/")
def root():
  return {"ok": True, "routes": ["/", "/health", "/tg (GET/POST)", "/notify", "/docs"]}

@app.get("/health")
def health():
  return {"ok": True, "service": "snab-bot", "webhook": "/tg"}

@app.get("/tg")
def get_tg():
  return {"ok": True, "route": "/tg"}

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

  text = format_order_text(data)

  kb = None
  if ready_for_receive(data.get("status", "")):
    cb = {"a": "set_received", "order_id": data.get("order_id"), "row": data.get("row_index")}
    kb = { "inline_keyboard": [[ { "text": "📦 ТМЦ ПОЛУЧЕНО", "callback_data": json.dumps(cb, ensure_ascii=False) } ]] }

  res = tg_send_message(text, kb)
  return {"ok": True, "telegram_response": res}

@app.post("/tg")
async def tg_post(req: Request):
  upd = await req.json()

  # обработка инлайн-кнопки
  if "callback_query" in upd:
    cq = upd["callback_query"]
    msg = cq.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    message_id = msg.get("message_id")
    data_raw = cq.get("data", "")

    try:
      data = json.loads(data_raw)
    except Exception:
      data = {}

    if data.get("a") == "set_received":
      # шлём в Apps Script
      if not SHEET_SCRIPT_URL or not SHEET_API_KEY:
        tg_answer_callback(cq.get("id"), "Не настроен SHEET_SCRIPT_URL или SHEET_API_KEY", True)
        return {"ok": False}

      payload = {
        "apiKey": SHEET_API_KEY,
        "action": "set_received",
        "orderId": data.get("order_id"),
        "row": data.get("row")  # ← точный номер строки
      }
      try:
        r = requests.post(SHEET_SCRIPT_URL, json=payload, timeout=20)
        js = r.json()
        ok = js.get("ok", False)
      except Exception as e:
        ok = False
        js = {"error": str(e)}

      # гасим кнопку (оставляем, но делаем неактивной)
      kb_disabled = { "inline_keyboard": [[ { "text": "✅ ТМЦ ПОЛУЧЕНО", "callback_data": "noop" } ]] }
      if chat_id and message_id:
        tg_edit_markup(chat_id, message_id, kb_disabled)

      tg_answer_callback(cq.get("id"), "Статус обновлён" if ok else "Не удалось обновить статус")
      return {"ok": True, "result": js}

  # прочие сообщения / команды — сюда можно добавить ваш парсер команд
  return {"ok": True}
