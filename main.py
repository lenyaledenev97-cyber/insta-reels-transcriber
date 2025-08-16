import os
import re
import asyncio
import logging
from typing import List

from fastapi import FastAPI, Request, HTTPException
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters
)
from yt_dlp import YoutubeDL
from openai import OpenAI

logging.basicConfig(level=logging.INFO)

# ========= Переменные окружения =========
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
WEBHOOK_SECRET     = os.getenv("WEBHOOK_SECRET", "").strip()
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY", "").strip()
ALLOWED_CHATS_RAW  = os.getenv("ALLOWED_CHATS", "").strip()  # "123,456" (необязательно)

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
        ALLOWED_CHATS = []

# OpenAI
oai = OpenAI(api_key=OPENAI_API_KEY)

# ========= Telegram =========
tg_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

RE_REELS = re.compile(
    r"(https?://(?:www\.)?instagram\.com/(?:reel|reels)/[^\s/?#]+(?:\?[^\s]*)?)",
    re.IGNORECASE,
)

def chat_allowed(chat_id: int) -> bool:
    return (not ALLOWED_CHATS) or (chat_id in ALLOWED_CHATS)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if not chat_allowed(update.effective_chat.id):
        return
    await update.message.reply_text(
        "Привет! Отправь ссылку на Instagram Reels — верну расшифровку. "
        "Проверяем, что бот жив 😉"
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
    await update.message.reply_text("Секунду, скачиваю и запускаю транскрибацию…")

    try:
        transcript = await download_and_transcribe(reels_url)
    except Exception as e:
        logging.exception("Ошибка транскрибации")
        await update.message.reply_text(
            f"Не удалось обработать видео: {e}\nПопробуй другую ссылку."
        )
        return

    if not transcript:
        await update.message.reply_text("Не удалось получить текст из видео 😕")
        return

    # Телеграм ограничивает длину сообщения ~4096, режем безопасно
    chunks = [transcript[i:i+3500] for i in range(0, len(transcript), 3500)]
    await update.message.reply_text("Готово! Расшифровка ниже 👇")
    for i, ch in enumerate(chunks, 1):
        prefix = f"Часть {i}/{len(chunks)}:\n" if len(chunks) > 1 else ""
        await update.message.reply_text(prefix + ch)

async def download_and_transcribe(url: str) -> str:
    """
    Скачиваем дорожку с помощью yt-dlp (bestaudio), затем отправляем в OpenAI.
    Модель: gpt-4o-mini-transcribe.
    """

    def _download_audio(tmpdir: str) -> str:
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(tmpdir, "%(id)s.%(ext)s"),
            "quiet": True,
            "noprogress": True,
            "nocheckcertificate": True,
            # при необходимости yt-dlp сам дернет ffmpeg для mux/кон
