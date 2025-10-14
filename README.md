# SahaResource Shipments Bot


## 🚀 Деплой на Render.com

### Вариант A — через Blueprint (render.yaml)
1. Залей проект в GitHub репозиторий.
2. В Render нажми **New → Blueprint** и укажи ссылку на репозиторий.
3. В разделе Environment добавь переменные:
   - `BOT_TOKEN` — токен бота из @BotFather
   - `CHAT_ID` — например `-1003141855190`
   - `WEBHOOK_SECRET` — длинная случайная строка
   - (опционально) `BASE_URL` — домен от Render (если планируешь `/set_webhook`)
4. Дождись билда — Render выдаст URL вида `https://<name>.onrender.com`.

### Вариант B — через Web Service
1. New → Web Service → выбери репозиторий.
2. Build Command: `pip install -r requirements.txt`
3. Start Command: `uvicorn app:app --host 0.0.0.0 --port $PORT`
4. Env Vars — как в пункте выше.

### Проверка
```bash
curl -s https://<your-render-url>/health
# {"ok": true}
curl -X POST https://<your-render-url>/notify   -H "Authorization: Bearer <WEBHOOK_SECRET>"   -H "Content-Type: application/json"   -d @sample_payload.json
```
После этого сообщение появится в группе.
