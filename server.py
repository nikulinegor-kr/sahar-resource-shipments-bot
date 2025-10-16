# server.py
import os, html, requests
from fastapi import FastAPI, Request, Header, HTTPException
from typing import Optional, Dict, Any

BOT_TOKEN        = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID          = os.getenv("CHAT_ID", "").strip()
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "").strip()
SHEET_SCRIPT_URL = os.getenv("SHEET_SCRIPT_URL", "").strip()
SHEET_API_KEY    = os.getenv("SHEET_API_KEY", "").strip()

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

app = FastAPI(title="Snab Notify Bot", version="1.3.0")

# ---------- Telegram helpers ----------
def tg_call(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
  url = f"{TG_API}/{method}"
  r = requests.post(url, json=payload, timeout=15)
  try:
    return r.json()
  except Exception:
    return {"ok": False, "status": r.status_code, "text": r.text}

def tg_send(text: str, reply_markup: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
  payload = {
    "chat_id": CHAT_ID,
    "text": text,
    "parse_mode": "HTML",
    "disable_web_page_preview": True
  }
  if reply_markup:
    payload["reply_markup"] = reply_markup
  return tg_call("sendMessage", payload)

def tg_edit_reply_markup(chat_id: str, message_id: int, reply_markup: Optional[Dict[str, Any]]):
  payload = {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup}
  return tg_call("editMessageReplyMarkup", payload)

def tg_answer_cbq(cbq_id: str, text: str, show_alert: bool = False):
  return tg_call("answerCallbackQuery", {"callback_query_id": cbq_id, "text": text, "show_alert": show_alert})

# ---------- Message render ----------
def render_text(data: Dict[str, Any]) -> str:
  g = lambda k: (data.get(k) or "").strip()
  lines = ["📦 Уведомление о заявке"]
  order = g("order_id") or "—"
  lines.append(f"🧾 Заявка: {html.escape(order)}")
  if g("priority"):   lines.append(f"⭐ Приоритет: {html.escape(g('priority'))}")
  if g("status"):     lines.append(f"🚚 Статус: {html.escape(g('status'))}")
  if g("carrier"):    lines.append(f"🚛 ТК: {html.escape(g('carrier'))}")
  if g("ttn"):        lines.append(f"📄 № ТТН: {html.escape(g('ttn'))}")
  if g("ship_date"):  lines.append(f"📅 Дата отгрузки: {html.escape(g('ship_date'))}")
  if g("arrival"):    lines.append(f"📅 Дата прибытия: {html.escape(g('arrival'))}")
  if g("applicant"):  lines.append(f"👤 Заявитель: {html.escape(g('applicant'))}")
  if g("comment"):    lines.append(f"📝 Комментарий: {html.escape(g('comment'))}")
  return "\n".join(lines)

def needs_button(status: str) -> bool:
  """Показывать кнопку только когда статус 'Доставлено в ТК' (регистр/пробелы игнорируем)."""
  s = (status or "").strip().lower()
  return s == "доставлено в тк"

# ---------- Routes ----------
@app.get("/")
def root():
  return {"ok": True, "routes": ["/", "/health", "/tg (GET/POST)", "/notify", "/docs"]}

@app.get("/health")
def health():
  return {"ok": True, "service": "snab-bot", "webhook": "/tg"}

@app.get("/tg")
def tg_get():
  return {"ok": True, "route": "/tg"}

# Получение уведомлений из Google Sheets
@app.post("/notify")
async def notify(req: Request, authorization: Optional[str] = Header(None)):
  if not authorization or not authorization.startswith("Bearer "):
    raise HTTPException(status_code=401, detail="Missing Authorization header")
  token = authorization.split("Bearer ", 1)[-1].strip()
  if token != WEBHOOK_SECRET:
    raise HTTPException(status_code=401, detail="Invalid token")

  try:
    data = await req.json()
  except Exception:
    raise HTTPException(status_code=400, detail="Invalid JSON")

  text = render_text(data)

  # Инлайн-кнопка только при статусе "Доставлено в ТК"
  kb = None
  row_index = data.get("row_index")
  status = (data.get("status") or "")
  if row_index and needs_button(status):
    kb = {
      "inline_keyboard": [[
        {"text": "📦 ТМЦ ПОЛУЧЕНО", "callback_data": f"rcv:{int(row_index)}"}
      ]]
    }

  res = tg_send(text, reply_markup=kb)
  if not res.get("ok"):
    raise HTTPException(status_code=502, detail=f"Telegram error: {res}")
  return {"ok": True, "sent": True}

# Telegram webhook: команды/кнопки
@app.post("/tg")
async def tg_webhook(req: Request):
  update = await req.json()
  # print("TG webhook:", update)  # можно включить для отладки

  # обработка callback_query (нажатие кнопки)
  if "callback_query" in update:
    cbq = update["callback_query"]
    data = cbq.get("data") or ""
    cbq_id = cbq.get("id")
    msg = cbq.get("message") or {}
    chat_id = str(msg.get("chat", {}).get("id") or CHAT_ID)
    message_id = msg.get("message_id")

    if data.startswith("rcv:"):
      try:
        row = int(data.split("rcv:",1)[1])
      except Exception:
        tg_answer_cbq(cbq_id, "Ошибка: некорректные данные.")
        return {"ok": True}

      if not SHEET_SCRIPT_URL or not SHEET_API_KEY:
        tg_answer_cbq(cbq_id, "Ошибка конфигурации сервера.")
        return {"ok": True}

      # вызов веб-хука Apps Script → выставить "Доставлено" в строке row
      try:
        r = requests.post(
          SHEET_SCRIPT_URL,
          json={"apiKey": SHEET_API_KEY, "action": "set_received", "row": row},
          timeout=15
        )
        ok = r.ok and r.json().get("ok")
      except Exception as e:
        ok = False

      if ok:
        tg_answer_cbq(cbq_id, "Статус обновлен: Доставлено.")
        # делаем кнопку неактивной: убираем клавиатуру
        if chat_id and message_id:
          tg_edit_reply_markup(chat_id, message_id, reply_markup={})
      else:
        tg_answer_cbq(cbq_id, "Не удалось обновить статус.")

    else:
      tg_answer_cbq(cbq_id, "Неизвестная команда.")
    return {"ok": True}

  # простые команды (минимум)
  msg = update.get("message") or update.get("channel_post") or {}
  text = (msg.get("text") or "").strip().lower()
  if text in ("/start", f"/start@{os.getenv('BOT_USERNAME','')}"):
    tg_send("Привет! Я бот уведомлений по заявкам. Команды: /today /week /my /priority /help")
  elif text in ("/help", f"/help@{os.getenv('BOT_USERNAME','')}"):
    tg_send("Доступные команды:\n/today – отгрузки/прибытия сегодня\n/week – за 7 дней\n/my – мои заявки\n/priority – аварийные")
  # (остальные команды можно дописать к вашему коду фильтрации)
  return {"ok": True}
