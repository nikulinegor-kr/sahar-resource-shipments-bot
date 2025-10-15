# server.py
import os, html, requests
from fastapi import FastAPI, Request, Header, HTTPException
from typing import Optional, Dict, Any

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID   = os.getenv("CHAT_ID", "").strip()   # дефолтный чат для /notify
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
SHEET_SCRIPT_URL = os.getenv("SHEET_SCRIPT_URL", "").strip()
SHEET_API_KEY = os.getenv("SHEET_API_KEY", "").strip()

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

app = FastAPI(title="Snab Notify Bot", version="1.4.0")

def esc(s: Optional[str]) -> str:
  return html.escape((s or "").strip())

def tg_send(chat_id: str|int, text: str, parse_mode="HTML", reply_markup: Optional[dict]=None):
  url = f"{TG_API}/sendMessage"
  payload = {
    "chat_id": chat_id,
    "text": text,
    "parse_mode": parse_mode,
    "disable_web_page_preview": True,
  }
  if reply_markup:
    payload["reply_markup"] = reply_markup
  r = requests.post(url, json=payload, timeout=15)
  try:
    return r.json()
  except Exception:
    return {"ok": False, "status": r.status_code, "text": r.text}

def tg_edit_reply_markup(chat_id: int, message_id: int, reply_markup: Optional[dict]):
  url = f"{TG_API}/editMessageReplyMarkup"
  r = requests.post(url, json={
    "chat_id": chat_id,
    "message_id": message_id,
    "reply_markup": reply_markup
  }, timeout=10)
  return r.json()

def tg_answer_cb(cb_id: str, text: str, alert: bool=False):
  url = f"{TG_API}/answerCallbackQuery"
  requests.post(url, json={"callback_query_id": cb_id, "text": text, "show_alert": alert}, timeout=10)

def keyboard_for_status(data: Dict[str, Any]) -> Optional[dict]:
  """Покажем кнопку только если статус = 'Доставлено в ТК'."""
  status = (data.get("status") or "").strip().lower()
  order  = (data.get("order_id") or "").strip()
  if not order:
    return None
  if status != "доставлено в тк":
    return None
  return {
    "inline_keyboard": [[
      {"text": "📥 ТМЦ ПОЛУЧЕНО", "callback_data": f"recv|{order}"}
    ]]
  }

def disabled_keyboard() -> dict:
  """Неактивная кнопка — формально кнопка остаётся, но с 'noop'."""
  return {
    "inline_keyboard": [[
      {"text": "✅ Получено", "callback_data": "noop"}
    ]]
  }

def format_order_text(data: Dict[str, Any]) -> str:
  g = lambda k: (data.get(k) or "").strip()
  lines = ["📦 Уведомление о заявке"]
  lines.append(f"🧾 Заявка: {esc(g('order_id') or '—')}")
  if g("priority"):   lines.append(f"⭐ Приоритет: {esc(g('priority'))}")
  if g("status"):     lines.append(f"🚚 Статус: {esc(g('status'))}")
  if g("carrier"):    lines.append(f"🚛 ТК: {esc(g('carrier'))}")
  if g("ttn"):        lines.append(f"📄 № ТТН: {esc(g('ttn'))}")
  if g("ship_date"):  lines.append(f"📅 Дата отгрузки: {esc(g('ship_date'))}")
  if g("arrival"):    lines.append(f"📅 Дата прибытия: {esc(g('arrival'))}")
  if g("applicant"):  lines.append(f"👤 Заявитель: {esc(g('applicant'))}")
  if g("comment"):    lines.append(f"📝 Комментарий: {esc(g('comment'))}")
  return "\n".join(lines)

@app.get("/")
def root():
  return {"ok": True, "routes": ["/", "/health", "/tg (GET/POST)", "/notify", "/docs"]}

@app.get("/health")
def health():
  return {"ok": True, "service": "snab-bot", "webhook": "/tg"}

@app.get("/tg")
def tg_get():
  return {"ok": True, "route": "/tg"}

# Таблица → бот: уведомление
@app.post("/notify")
async def notify(req: Request, authorization: Optional[str] = Header(None)):
  if not authorization or not authorization.startswith("Bearer "):
    raise HTTPException(status_code=401, detail="Missing Authorization header")
  token = authorization.split("Bearer ", 1)[-1].strip()
  if token != WEBHOOK_SECRET:
    raise HTTPException(status_code=401, detail="Invalid token")

  data = await req.json()
  text = format_order_text(data)
  kb = keyboard_for_status(data)  # если «Доставлено в ТК» — добавим кнопку
  r = tg_send(CHAT_ID, text, reply_markup=kb)
  return {"ok": True, "telegram_response": r}

# Telegram webhook
@app.post("/tg")
async def tg_webhook(req: Request):
  upd = await req.json()
  print("TG update:", upd)

  # Нажатие на кнопку
  if "callback_query" in upd:
    cq = upd["callback_query"]
    cb_id = cq.get("id")
    msg = cq.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    message_id = msg.get("message_id")
    data = (cq.get("data") or "").strip()

    if data.startswith("recv|"):
      order_id = data.split("|", 1)[1]

      # Шлём в Google Apps Script — установить статус «Доставлено»
      if not SHEET_SCRIPT_URL or not SHEET_API_KEY:
        tg_answer_cb(cb_id, "Ошибка конфигурации (нет SHEET_SCRIPT_URL/SHEET_API_KEY)", True)
        return {"ok": False}

      try:
        r = requests.post(SHEET_SCRIPT_URL, json={
          "api_key": SHEET_API_KEY,
          "action": "mark_received",
          "order_id": order_id
        }, timeout=15)
        js = r.json()
      except Exception as e:
        tg_answer_cb(cb_id, f"Ошибка связи с таблицей: {e}", True)
        return {"ok": False}

      if not js.get("ok"):
        tg_answer_cb(cb_id, f"Не удалось обновить: {js.get('error','unknown')}", True)
        return {"ok": False}

      # Успех/уже отмечено — делаем кнопку неактивной
      tg_edit_reply_markup(chat_id, message_id, disabled_keyboard())
      if js.get("already"):
        tg_answer_cb(cb_id, "Уже было отмечено ранее")
      else:
        tg_answer_cb(cb_id, "Готово: статус «Доставлено»")
      return {"ok": True}

    # Неактивная кнопка (noop)
    if data == "noop":
      tg_answer_cb(cb_id, "Статус уже отмечен", False)
      return {"ok": True}

    # Прочие — просто квитируем
    tg_answer_cb(cb_id, "OK")
    return {"ok": True}

  # Сообщения (для /start, /help, /id)
  msg = upd.get("message") or upd.get("channel_post")
  if msg:
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()
    cmd = text.split()[0].lower() if text else ""
    if "@" in cmd:
      cmd = cmd.split("@", 1)[0]

    if cmd == "/start":
      tg_send(chat_id,
              "👋 Привет! Я бот снабжения.\n"
              "Доступные команды:\n"
              "• /help — список команд\n"
              "• /id — показать ваш Telegram ID")
    elif cmd == "/help":
      tg_send(chat_id,
              "🛠 Команды:\n"
              "/id — показать ваш Telegram ID\n"
              "Уведомления из таблицы приходят автоматически.")
    elif cmd == "/id":
      uid = msg.get("from", {}).get("id")
      tg_send(chat_id, f"🧾 Ваш Telegram ID: <code>{uid}</code>")
  return {"ok": True}

if __name__ == "__main__":
  import uvicorn
  uvicorn.run("server:app", host="0.0.0.0", port=8000)
