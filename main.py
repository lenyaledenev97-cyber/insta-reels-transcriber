import os
import re
import asyncio
import tempfile
from typing import List, Optional

from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ==== Конфигурация через переменные окружения ====
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
ALLOWED_CHATS_RAW = os.getenv("ALLOWED_CHATS", "").strip()
TRANSCRIBE_MODEL = os.getenv("TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN не задан как переменная окружения")
if not WEBHOOK_SECRET:
    raise RuntimeError("WEBHOOK_SECRET не задан как переменная окружения")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY не задан как переменная окружения")

# ALLOWED_CHATS: опционально ограничиваем, кто может пользоваться ботом
ALLOWED_CHATS: List[int] = []
if ALLOWED_CHATS_RAW:
    try:
        ALLOWED_CHATS = [int(x) for x in ALLOWED_CHATS_RAW.split(",") if x.strip()]
    except Exception:
        ALLOWED_CHATS = []  # на всякий случай игнорируем неверный формат

# OpenAI SDK
from openai import OpenAI
oai = OpenAI(api_key=OPENAI_API_KEY)

# Telegram app (webhook-режим)
tg_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

# FastAPI
app = FastAPI(title="Insta Reels Transcriber")

# ----------------- Утилиты -----------------
RE_ELIGIBLE_URL = re.compile(r"(https?://(www\.)?instagram\.com/reel/[^ \n]+)", re.IGNORECASE)

def split_text(text: str, limit: int = 3800) -> List[str]:
    """Безопасно режем ответ под лимиты Telegram (около 4096)."""
    parts, cur = [], []
    cur_len = 0
    for line in text.splitlines(True):
        if cur_len + len(line) > limit and cur:
            parts.append("".join(cur))
            cur, cur_len = [], 0
        cur.append(line)
        cur_len += len(line)
    if cur:
        parts.append("".join(cur))
    return parts

def user_allowed(chat_id: int) -> bool:
    return (not ALLOWED_CHATS) or (chat_id in ALLOWED_CHATS)

# ----------------- Транскрибация -----------------
async def download_audio_from_instagram(url: str) -> str:
    """
    Скачиваем аудио дорожку из Reels с помощью yt-dlp.
    Возвращаем путь к временному файлу .mp3/.m4a
    """
    import yt_dlp

    tmpdir = tempfile.mkdtemp(prefix="reel_")
    outtmpl = os.path.join(tmpdir, "%(id)s.%(ext)s")

    ydl_opts = {
        "outtmpl": outtmpl,
        "format": "m4a/bestaudio/best",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
        "quiet": True,
        "noprogress": True,
    }

    loop = asyncio.get_event_loop()
    def _run():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # после постпроцессинга будет mp3
            base = ydl.prepare_filename(info)
            mp3 = os.path.splitext(base)[0] + ".mp3"
            return mp3

    audio_path = await loop.run_in_executor(None, _run)
    return audio_path

async def transcribe_file(filepath: str) -> str:
    """
    Отправляем файл на OpenAI для транскрибации.
    По умолчанию — модель gpt-4o-mini-transcribe (быстро/дёшево).
    Можно заменить через переменную окружения TRANSCRIBE_MODEL.
    """
    with open(filepath, "rb") as f:
        if TRANSCRIBE_MODEL.lower().startswith("whisper"):
            # старый эндпоинт
            res = oai.audio.transcriptions.create(
                file=f,
                model=TRANSCRIBE_MODEL,
                response_format="text",
            )
            return res  # уже строка
        else:
            # новый быстрый транскрайбер
            res = oai.audio.transcriptions.create(
                file=f,
                model=TRANSCRIBE_MODEL,
                response_format="text",
            )
            return res

# ----------------- Telegram Handlers -----------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    chat_id = update.message.chat_id
    if not user_allowed(chat_id):
        await update.message.reply_text("⛔ Этот бот для приватного использования.")
        return

    await update.message.reply_text(
        "Привет! Отправь ссылку на Instagram Reels — верну расшифровку.\n"
        "Пока что проверяем, что бот жив 😉"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    chat_id = update.message.chat_id
    if not user_allowed(chat_id):
        return

    text = update.message.text or ""
    m = RE_ELIGIBLE_URL.search(text)
    if not m:
        await update.message.reply_text("Пришли ссылку на Reels, пожалуйста.")
        return

    url = m.group(1)
    msg = await update.message.reply_text("Скачиваю видео и извлекаю аудио… ⏬")

    try:
        audio_path = await download_audio_from_instagram(url)
        await msg.edit_text("Транскрибирую аудио… 🎧")

        transcript = await transcribe_file(audio_path)
        if not transcript.strip():
            await msg.edit_text("Не удалось получить текст 😔")
            return

        parts = split_text(transcript)
        await msg.edit_text("Готово! Отправляю текст… 📄")
        for i, p in enumerate(parts, 1):
            header = f"Часть {i}/{len(parts)}:\n" if len(parts) > 1 else ""
            await update.message.reply_text(header + p)

    except Exception as e:
        await msg.edit_text(f"Ошибка при обработке: {e}")
    finally:
        # подчистим временные файлы
        try:
            if audio_path and os.path.exists(audio_path):
                os.remove(audio_path)
        except Exception:
            pass

# Регистрируем хендлеры
tg_app.add_handler(CommandHandler("start", start_cmd))
tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

# ----------------- Webhook -----------------
class TGUpdate(BaseModel):
    update_id: Optional[int] = None
    message: Optional[dict] = None

@app.post(f"/webhook/{WEBHOOK_SECRET}")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")

    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return {"ok": True}

@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

# Локальный запуск (не используется в Cloud Run, но полезно для отладки)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
