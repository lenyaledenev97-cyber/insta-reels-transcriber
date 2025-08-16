# Dockerfile — минимальный образ для Cloud Run
FROM python:3.11-slim

# Обновления и ffmpeg (на случай, если yt-dlp захочет постпроцессинг)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Создадим каталог приложения
WORKDIR /app

# Установим зависимости напрямую (без requirements.txt)
RUN pip install --no-cache-dir \
    "fastapi==0.112.*" \
    "uvicorn[standard]==0.30.*" \
    "python-telegram-bot==21.6" \
    "yt-dlp>=2024.10.22" \
    "openai>=1.40.0"

# Копируем код
COPY main.py /app/main.py

# Cloud Run передаст порт в $PORT
ENV PORT=8080
EXPOSE 8080

# Запуск FastAPI (в объекте fastapi_app)
CMD exec uvicorn main:fastapi_app --host 0.0.0.0 --port $PORT
