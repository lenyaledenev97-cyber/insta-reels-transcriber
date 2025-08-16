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
    Application, CommandHandler, MessageHandler, ContextTypes,
    filters
)

from yt_dlp import YoutubeDL
from openai import OpenAI

# ---------- Конфигурация через переменные окружения ----------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
WEBHOOK_SECRET     = os.getenv("WEBHOOK_SECRET", "").strip()
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY", "").strip()
ALLOWED_CHATS_RAW  = os.getenv("ALLOWED_CHATS", "").strip()  # "123,456"

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN не задан как переменная окружения")
if not WEBHOOK_SECRET:
    raise RuntimeError("WEBHOOK_SECRET не задан как переменная окружения")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY не задан как переменная окружения")

ALLOWED_CHATS: List[int] = []
if ALLOWED_CHATS_RAW:
    try:
        ALLOWED_CHATS = [int(x) for x in ALLOWED_CHATS_RAW.split(",") if x.strip()]
    except Exception:
        # Если формат неверный — оставим пустым, чтобы не блокировать запуск
        ALLOWED_CHATS = []

# OpenAI клиент
oai = OpenAI(api_key=OPENAI_API_KEY)

# ---------- Telegram ----------
app_tg = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

# Простой регекс для ссылок на Instagram Reels
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
        "Пока что проверяем, что бот жив 😉"
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
    await update.message.reply_text("Секунду, скачиваю видео и запускаю транскрибацию…")

    try:
        transcript = await download_and_transcribe(reels_url)
    except Exception as e:
        logging.exception("Ошибка транскрибации")
        await update.message.reply_text(
            f"Не получилось обработать видео: {e}\n"
            "Проверь ссылку или пришли другую."
        )
        return

    if not transcript:
        await update.message.reply_text("Не удалось получить текст из видео 😕")
        return

    # Режем по 3500 символов на случай длинной речи
    chunks = [transcript[i:i+3500] for i in range(0, len(transcript), 3500)]
    await update.message.reply_text("Готово! Расшифровка ниже 👇")
    for i, ch in enumerate(chunks, 1):
        prefix = f"Часть {i}/{len(chunks)}:\n" if len(chunks) > 1 else ""
        await update.message.reply_text(prefix + ch)


async def download_and_transcribe(url: str) -> str:
    """
    Скачиваем только аудио (без ffmpeg), отдаём в OpenAI gpt-4o-mini-transcribe.
    OpenAI принимает аудио-форматы mp4/m4a/webm/mp3/wav и т.п.
    """
    # yt-dlp блокирующий — выносим в отдельный поток
    def _download_audio(tmpdir: str) -> str:
        # Скачиваем bestaudio, сохраняем в tmpdir. Выходной путь получим из результата.
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(tmpdir, "%(id)s.%(ext)s"),
            "quiet": True,
            "noprogress": True,
            "nocheckcertificate": True,
            # Иногда IG просит куки, но для публичных роликов обычного хватает
        }
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            return filename  # путь к загруженному файлу (чаще .m4a или .mp4)

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        audio_path = await asyncio.to_thread(_download_audio, tmp)

        # Отправляем в OpenAI на транскрибацию
        with open(audio_path, "rb") as f:
            resp = oai.audio.transcriptions.create(
                model="gpt-4o-mini-transcribe",
                file=f,
            )
        # У разных версий SDK ответ может быть в resp.text или resp["text"]
        text = getattr(resp, "text", None) or getattr(resp, "text_output", None)
        if not text and isinstance(resp, dict):
            text = resp.get("text")
        return text or ""

# Регистрируем хендлеры
app_tg.add_handler(CommandHandler("start", cmd_start))
app_tg.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))

# ---------- FastAPI (webhook) ----------
class TelegramUpdate(BaseModel):
    update_id: int | None = None

app = FastAPI()

@fastapi_app.get("/")
async def health():
    return {"ok": True}

@fastapi_app.post(f"/webhook/{{secret}}")
async def webhook(secret: str, request: Request):
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    data = await request.json()
    update = Update.de_json(data, app_tg.bot)
    await app_tg.process_update(update)
    return {"ok": True}

# Локальный запуск (не используется в Cloud Run)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(fastapi_app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
