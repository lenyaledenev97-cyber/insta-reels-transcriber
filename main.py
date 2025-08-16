# main.py
import os
import re
import asyncio
import logging
from typing import List

from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters
)

from yt_dlp import YoutubeDL
from openai import OpenAI

# -------- Переменные окружения (Cloud Run → Variables & Secrets) --------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
WEBHOOK_SECRET     = os.getenv("WEBHOOK_SECRET", "").strip()           # ваш mysupersecret789
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY", "").strip()
ALLOWED_CHATS_RAW  = os.getenv("ALLOWED_CHATS", "").strip()            # опционально: "123,456"

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN не задан как переменная окружения")
if not WEBHOOK_SECRET:
    raise RuntimeError("WEBHOOK_SECRET не задан как переменная окружения")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY не задан как переменная окружения")

# Разрешённые чаты (если пусто — пускаем всех)
ALLOWED_CHATS: List[int] = []
if ALLOWED_CHATS_RAW:
    try:
        ALLOWED_CHATS = [int(x) for x in ALLOWED_CHATS_RAW.split(",") if x.strip()]
    except Exception:
        ALLOWED_CHATS = []

# OpenAI клиент
oai = OpenAI(api_key=OPENAI_API_KEY)

# -------- Telegram bot (python-telegram-bot v21) --------
tg_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

# Ссылки на Reels
RE_REELS = re.compile(
    r"(https?://(?:www\.)?instagram\.com/(?:reel|reels)/[^\s/?#]+(?:\?[^\s]*)?)",
    re.IGNORECASE
)

def chat_allowed(chat_id: int) -> bool:
    if not ALLOWED_CHATS:
        return True
    return chat_id in ALLOWED_CHATS

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not chat_allowed(update.effective_chat.id):
        return
    await update.message.reply_text(
        "Привет! Отправь ссылку на Instagram Reels — верну расшифровку.\n"
        "Модель: gpt-4o-mini-transcribe 🎙️"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    chat_id = update.effective_chat.id
    if not chat_allowed(chat_id):
        return

    text = (update.message.text or "").strip()
    m = RE_REELS.search(text)
    if not m:
        await update.message.reply_text("Пришли ссылку на Reels, пожалуйста.")
        return

    reels_url = m.group(1)
    await update.message.reply_text("Секунду, скачиваю аудио и запускаю транскрибацию…")

    try:
        transcript = await download_and_transcribe(reels_url)
    except Exception as e:
        logging.exception("Ошибка транскрибации")
        await update.message.reply_text(
            f"Не получилось обработать видео: {e}\nПопробуйте другую ссылку."
        )
        return

    if not transcript:
        await update.message.reply_text("Не удалось получить текст из видео 😕")
        return

    chunks = [transcript[i:i+3500] for i in range(0, len(transcript), 3500)]
    await update.message.reply_text("Готово! Расшифровка ниже 👇")
    for i, ch in enumerate(chunks, 1):
        prefix = f"Часть {i}/{len(chunks)}:\n" if len(chunks) > 1 else ""
        await update.message.reply_text(prefix + ch)


async def download_and_transcribe(url: str) -> str:
    """
    Скачиваем аудио дорожку через yt-dlp и отправляем в OpenAI
    (модель: gpt-4o-mini-transcribe).
    """
    def _download_audio(tmpdir: str) -> str:
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(tmpdir, "%(id)s.%(ext)s"),
            "quiet": True,
            "noprogress": True,
            # Без обязательного постпроцесса (но на всякий случай ffmpeg есть в образе)
        }
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        audio_path = await asyncio.to_thread(_download_audio, tmp)
        with open(audio_path, "rb") as f:
            resp = oai.audio.transcriptions.create(
                model="gpt-4o-mini-transcribe",
                file=f,
            )
        text = getattr(resp, "text", None) or getattr(resp, "text_output", None)
        if not text and isinstance(resp, dict):
            text = resp.get("text")
        return text or ""

# -------- Роуты FastAPI (вебхук для Cloud Run) --------
fastapi_app = FastAPI()

@fastapi_app.get("/")
async def health():
    return {"ok": True}

@fastapi_app.post(f"/webhook/{{secret}}")
async def webhook(secret: str, request: Request):
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    data = await request.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return {"ok": True}

# Локальный запуск (не используется в Cloud Run)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(fastapi_app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
