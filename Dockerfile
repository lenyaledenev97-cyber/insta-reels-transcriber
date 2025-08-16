FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# зависимости (можно через requirements.txt — ок оставить так)
RUN pip install --no-cache-dir \
    "fastapi==0.115.*" \
    "uvicorn[standard]==0.30.*" \
    "python-telegram-bot==21.6" \
    "yt-dlp>=2024.10.22" \
    "openai>=1.40.0"

COPY main.py /app/main.py

ENV PORT=8080
EXPOSE 8080

CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
