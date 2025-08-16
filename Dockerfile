# Базовый образ
FROM python:3.11-slim

# Установка зависимостей системы (для ffmpeg и yt-dlp)
RUN apt-get update && apt-get install -y \
    ffmpeg curl git \
    && rm -rf /var/lib/apt/lists/*

# Рабочая директория
WORKDIR /app

# Скопировать файлы проекта
COPY requirements.txt /app/requirements.txt
COPY main.py /app/main.py

# Установка Python-зависимостей
RUN pip install --no-cache-dir -r requirements.txt

# Открыть порт
EXPOSE 8080

# Запуск приложения (FastAPI + Telegram webhook)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
