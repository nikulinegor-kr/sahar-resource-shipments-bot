# --- базовый образ ---
FROM python:3.11-slim

# --- рабочая директория внутри контейнера ---
WORKDIR /app

# --- установка зависимостей ---
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- копируем весь проект ---
COPY . .

# --- порт, который слушает uvicorn ---
ENV PORT=8000

# --- команда запуска ---
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
